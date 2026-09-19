"""Exact record-family selection; never infer a new contract from defaults."""


def record_family(value, legacy_type, history_type, history_version):
    if type(value) is legacy_type:
        return "legacy"
    if type(value) is history_type:
        return "history_v2"
    if type(value) is not dict or any(type(key) is not str for key in value):
        return "invalid"
    if "contract_version" not in value:
        return "legacy"
    if (
        type(value["contract_version"]) is str
        and value["contract_version"] == history_version
    ):
        return "history_v2"
    return "invalid"
