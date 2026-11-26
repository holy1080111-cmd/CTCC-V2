# 開啟 CTCC 工具

安裝並完成這個版本的設定後，雙擊發行目錄內的 `source/Open-CTCC.cmd`，
選擇設定精靈產生的 `tool-profile.json`。程式會先核對設定、版本與本機服務，
完成後才開啟頁面。啟動核對可能需要幾十秒。

設定檔決定開啟分析、Demo 或 Live 工具。程式不會自動挑選舊設定，
不會代為設定帳戶，也不會因為開啟頁面而授予交易權限。Live 預設關閉；
原本的當次人工確認程序仍然適用。

Paper 的設定精靈會產生 `paper-profile.json`。請開啟已設定的「CTCC 2.0 Paper」
捷徑，或依 [Windows Paper 安裝說明](windows_tool_physical0058_v1.md#persistent-paper-on-windows)
使用 `-Tool paper` 檢查並啟動本機帳本。若在 `Open-CTCC.cmd` 的檔案選擇器
選到 Paper 設定檔，程式會顯示 Paper 啟動方式。

若尚未安裝隔離執行環境，啟動器會提示先完成安裝。設定不屬於這個版本、
核對失敗或連接埠已被其他程式占用時，頁面不會開啟。
若服務已完成核對但瀏覽器未能開啟，提示會提供固定的本機網址；服務會保留。
正常開啟後，仍須在頁面輸入私人目錄中的本機存取碼。不要將存取碼放入網址、
聊天、日誌或發行檔。

啟動後會保留「CTCC 工具」小視窗。關閉瀏覽器分頁不會停止本機工具；
可按「重新開啟頁面」回到同一次啟動。結束使用時，按「停止本機工具」並確認。
這只會停止本機程式，不會平倉、取消交易所保護單或清除交易紀錄。
若停止時仍有未完成操作，下次啟動必須重新核對結果。關閉管理視窗也會先詢問。

建立新的 Demo 設定時，可以選擇連結這台電腦已核對的 Notion 設定，預設略過。
需提供既有目的地設定檔、該檔已核對的 SHA256，以及私人權杖檔的路徑；
請勿輸入權杖內容。精靈只在本機核對權限、檔案雜湊與四個欄位設定，
不會在此步驟連線 Notion，也不會讀取舊 `.env` 或修改舊設定。
連結成功後會建立這份新設定專用的空白報告目錄；報告佇列與同步程式仍關閉。
後續獨立啟用同步時仍須重新核對真實 Notion 欄位；此步驟不代表已送出報告。

## Source and process boundary

The tracked batch file calls only this release's `runtime/Scripts/pythonw.exe`
and fixed source script with isolated Python flags. It accepts no batch
arguments and changes no PowerShell policy. A native existing-file picker makes
the user's profile choice; no credential prompt or automatic configuration is
part of launch.

On Windows, the virtual-environment executable is a redirector. The child
therefore uses the genuine isolated interpreter's absolute base executable,
with a freshly source-derived `__PYVENV_LAUNCHER__` naming this release's exact
`runtime/Scripts/python.exe`. Ambient launcher variables are stripped. This
is the protocol used by [CPython 3.12 Windows multiprocessing](https://github.com/python/cpython/blob/v3.12.14/Lib/multiprocessing/popen_spawn_win32.py).
The child independently verifies `sys.executable`, `sys.prefix` and all 41
locked distributions. The parent retains the actual native process handle
and checks its PID, creation time, executable image path and SHA256; the PID
must equal the native TCP listener owner. No process-tree or existing-PID
fallback is supported.

The existing private profile independently pins the exact package. Parent and
child reread that owner-only profile and bind its byte hash. The hash and a fresh
readiness nonce travel only through the newly created child's bounded stdin
pipe. No API token or credential enters process arguments, environment, output,
readiness metadata or browser URLs. The child uses the existing closed settings
loader and exact isolated dependency inventory.

Only launcher-wrapped applications expose `/_ctcc/desktop-ready/v1`. It accepts
an exact loopback peer/server/Host and same-origin GET without query or private
headers. The fixed response contains only child PID, nonce, mode and public
package/source identity, with no account or Arm state. Ordinary application and
lifespan handling are delegated unchanged. Manual CLI launch without the
desktop flag does not add this endpoint.

The parent checks native Windows TCP listener ownership before its zero-proxy,
zero-redirect readiness GET and again before opening the browser. It requires
the exact newly created child PID, nonce and package, rather than accepting a
successful response from another process. A port conflict never opens a browser.
Only its own new child can be terminated on failed startup; existing services,
databases, journals and protection are never selected.

The native supervisor retains an opaque same-process session for that original
child. Reopening rereads the matching profile/package and repeats native
handle/image, listener PID, nonce and readiness checks. A later launcher still
refuses a port collision. Stopping requires an explicit native confirmation
and terminates only the held child process handle. It adds no HTTP shutdown,
Arm, order, position-close or protection-cancel authority. A forced stop may
leave a pending operation requiring the existing durable recovery procedure.
The supervisor explicitly shows its own window and checks visibility after
the inherited first-show state; a hidden CLI parent cannot silently hide the
operator controls. This changes no system-wide visibility or security setting.
That visibility check concerns the window's own visible flag. It does not prove
the interactive Windows desktop is unlocked or that an input helper can
activate the window. Native GUI acceptance requires an unlocked desktop. A
failed activation alone does not establish a hidden-startup root cause; the
read-only session-state observation must be reported separately.

The desktop helper supplies no qualification, Arm, reconciliation, order or
release authority. It cannot migrate DB0017, adopt an older private profile,
choose a live account, clear a stop or substitute for exact-source CI.
