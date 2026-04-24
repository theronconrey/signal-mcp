from hollerback.goosed_client import _read_goose_config_defaults


def test_reads_provider_and_model(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("GOOSE_PROVIDER: openai\nGOOSE_MODEL: gpt-4o\n")
    assert _read_goose_config_defaults(cfg) == ("openai", "gpt-4o")


def test_missing_keys_return_none(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("SOME_OTHER_KEY: value\n")
    assert _read_goose_config_defaults(cfg) == (None, None)


def test_missing_file_returns_none(tmp_path):
    assert _read_goose_config_defaults(tmp_path / "does_not_exist.yaml") == (None, None)


def test_empty_file_returns_none(tmp_path):
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("")
    assert _read_goose_config_defaults(cfg) == (None, None)


def test_malformed_yaml_returns_none(tmp_path):
    cfg = tmp_path / "broken.yaml"
    cfg.write_text("key: [unclosed\n")
    assert _read_goose_config_defaults(cfg) == (None, None)


def test_partial_keys(tmp_path):
    cfg = tmp_path / "partial.yaml"
    cfg.write_text("GOOSE_PROVIDER: anthropic\n")
    assert _read_goose_config_defaults(cfg) == ("anthropic", None)
