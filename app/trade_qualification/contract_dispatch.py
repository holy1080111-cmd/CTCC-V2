"""Exact record-family selection; never infer a new contract from defaults."""


def record_family(value, legacy_type, history_type, history_version, *additional):
    return record_versions(
        value, legacy_type, ((history_type, history_version, "history_v2"), *additional)
    )


def record_versions(value, legacy_type, variants):
    if type(value) is legacy_type:
        return "legacy"
    for model, _version, tag in variants:
        if type(value) is model:
            return tag
    if (
        type(value) is not dict
        or len(value) > 64
        or any(type(key) is not str for key in value)
    ):
        return "invalid"
    if "contract_version" not in value:
        return "legacy"
    if type(value["contract_version"]) is str:
        for _model, version, tag in variants:
            if value["contract_version"] == version:
                return tag
    return "invalid"
