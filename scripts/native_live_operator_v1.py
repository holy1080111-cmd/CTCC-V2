"""Manual one-shot Live tool. Default command only checks local configuration.

No DSN, key, token, UID or IP argument is accepted. --run-once merely prepares
the genuine source/challenge; a current human must type all exact phrases.
"""

import argparse
import asyncio
import os
import sys

from app.domain.source_primitives import decode
from app.okx_live import native_runtime_v1 as runtime


async def _read_human_line(prompt):
    # A cancellable terminal reader: no abandoned input thread can keep a
    # stopped one-shot process alive after its challenge times out.
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars = []
    while True:
        character = None
        if os.name == "nt":
            import msvcrt

            if msvcrt.kbhit():
                character = msvcrt.getwch()
        else:
            import select

            if select.select((sys.stdin,), (), (), 0)[0]:
                raw = os.read(sys.stdin.fileno(), 1)
                if not raw:
                    raise EOFError
                character = chr(raw[0])
        if character is None:
            await asyncio.sleep(0.05)
            continue
        if character == "\x03":
            raise KeyboardInterrupt
        if character in ("\r", "\n"):
            sys.stdout.write("\n")
            return "".join(chars)
        if character in ("\b", "\x7f"):
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
        else:
            chars.append(character)
            sys.stdout.write(character)
        sys.stdout.flush()


async def _run(args):
    async with runtime.configured_live_runtime_v1() as configured:
        if not args.run_once:
            print("NATIVE_LIVE_CONFIGURED=1")
            print("LIVE_ARMED=0")
            print("EXECUTION_AUTHORITY=0")
            return 0
        from app.okx_live import owned_lifecycle_v1 as lifecycle
        from app.okx_live import owned_qualification_v1 as qualification

        async with qualification.open_live_operator_session_v1(
            configured,
            instrument_id=args.instrument,
            strategy=args.strategy,
            direction=args.direction,
            controlled_exit_authorized=args.controlled_roundtrip,
        ) as session:
            challenge = decode(session.challenge_data, maximum=16384)
            print("真實資金一次性驗收；最多一筆，等待你的當下確認。")
            print(
                "商品："
                + challenge["instrument_id"]
                + "；方向："
                + challenge["direction"]
            )
            print(
                "最高名義金額："
                + challenge["caps"][3]
                + " USDT；最高結構風險："
                + challenge["caps"][0]
                + " USDT"
            )
            print("輸入下列完整確認字句；逾時或驗證失敗會取消。")

            async def collect_human_answers():
                answers = []
                for name in (
                    "arm_phrase",
                    "clear_stop_phrase",
                    "controlled_exit_phrase",
                ):
                    phrase = challenge[name]
                    answers.append(
                        await _read_human_line(phrase + "\n> ") if phrase else ""
                    )
                qualification.signal_live_operator_approval_v1(
                    challenge_id=challenge["challenge_id"],
                    arm_phrase=answers[0],
                    clear_stop_phrase=answers[1],
                    controlled_exit_phrase=answers[2],
                )

            reader = asyncio.create_task(collect_human_answers())
            try:
                owner, handle = await qualification.run_owned_live_operator_once_v1(
                    session
                )
            finally:
                reader.cancel()
                try:
                    await reader
                except asyncio.CancelledError:
                    pass
            result = await lifecycle.reconcile_owned_live_postsubmit_v1(owner, handle)
            # The lifecycle returns redacted DATA. Do not print raw source,
            # account IDs, response bodies, credentials or private paths.
            print("LIVE_LIFECYCLE_OUTCOME=" + result["outcome"])
            print("LIVE_HOLD_RELEASED=" + str(int(result["hold_released"])))
            print("EXECUTION_AUTHORITY=0")
            return (
                0
                if result["outcome"] in ("ZERO_FILL_FLAT", "FLAT_EXIT")
                and result["hold_released"]
                else 2
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--instrument")
    parser.add_argument("--strategy")
    parser.add_argument("--direction", choices=("long", "short"))
    parser.add_argument("--controlled-roundtrip", action="store_true")
    args = parser.parse_args(argv)
    if args.run_once and not all((args.instrument, args.strategy, args.direction)):
        parser.error("--run-once requires instrument, strategy and direction")
    try:
        return asyncio.run(_run(args))
    except runtime.LiveRuntimeNotConfigured:
        print("NATIVE_LIVE_NOT_CONFIGURED=1")
        print("LIVE_ARMED=0")
        return 2
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("NATIVE_LIVE_CANCELLED=1")
        return 2
    except Exception:  # noqa: BLE001 -- private provider/SQL/configuration text must not escape.
        print("NATIVE_LIVE_STOPPED_RECONCILIATION_REQUIRED=1")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
