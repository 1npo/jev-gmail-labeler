import json
from pathlib import Path

import pytest

from jev_gmail_labeler import config as cfg
from jev_gmail_labeler.errors import ConfigError

CONFIG_DIR = Path('/home/u/.config/jev-gmail-labeler')
STATE_DIR = Path('/home/u/.local/state/jev-gmail-labeler')


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    """Fixed dirs and cwd; ``fake_files`` maps path -> JSON data (or an exception)."""
    monkeypatch.setattr(cfg, 'default_config_dir', lambda: CONFIG_DIR)
    monkeypatch.setattr(cfg, 'default_state_dir', lambda: STATE_DIR)
    monkeypatch.setattr(Path, 'cwd', classmethod(lambda cls: Path('/cwd')))
    fake_files: dict[Path, object] = {}

    def read_json(path):
        if path not in fake_files:
            raise FileNotFoundError(path)
        value = fake_files[path]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(cfg.files, 'read_json', read_json)
    return fake_files


@pytest.fixture
def fake_files(fake_env):
    return fake_env


def load(cli=None, config_path=None, config_json=None, environ=None):
    return cfg.load_config(
        cli=cli or {},
        config_path=config_path,
        config_json=config_json,
        environ=environ or {},
    )


def test_defaults():
    c = load()
    assert c.credentials_file == CONFIG_DIR / 'credentials.json'
    assert c.token_file == CONFIG_DIR / 'token.json'
    assert c.criteria_file == CONFIG_DIR / 'criteria.json'
    assert c.state_db == STATE_DIR / 'state.sqlite3'
    assert c.typesafe_api_key_file is None
    assert c.jev_model == 'jev-latest'
    assert c.jev_timeout_seconds == 10.0
    assert c.gmail_user == 'me'
    assert c.pubsub_topic is None
    assert c.max_body_chars == 8000
    assert c.spacy_model == 'en_core_web_md'
    assert c.log_level == 'INFO'
    assert c.gmail_quota_units_per_minute == 5000
    assert c.config_file is None


def test_real_default_dirs_use_platformdirs(monkeypatch):
    monkeypatch.undo()
    assert cfg.default_config_dir().name == 'jev-gmail-labeler'
    assert cfg.default_state_dir().name == 'jev-gmail-labeler'


def test_env_overrides_default():
    c = load(environ={'JEV_LABELER_MAX_BODY_CHARS': '500', 'JEV_LABELER_JEV_MODEL': 'm1'})
    assert c.max_body_chars == 500
    assert c.jev_model == 'm1'


def test_file_overrides_env(fake_files):
    fake_files[CONFIG_DIR / 'config.json'] = {'jev_model': 'from-file'}
    c = load(environ={'JEV_LABELER_JEV_MODEL': 'from-env'})
    assert c.jev_model == 'from-file'
    assert c.config_file == CONFIG_DIR / 'config.json'


def test_config_json_overrides_file(fake_files):
    fake_files[Path('/etc/c.json')] = {'jev_model': 'from-file', 'gmail_user': 'file'}
    c = load(config_path=Path('/etc/c.json'), config_json='{"jev_model": "from-json"}')
    assert c.jev_model == 'from-json'
    assert c.gmail_user == 'file'


def test_cli_overrides_config_json():
    c = load(cli={'jev_model': 'from-cli'}, config_json='{"jev_model": "from-json"}')
    assert c.jev_model == 'from-cli'


def test_cli_none_values_ignored_and_unknown_ignored():
    c = load(cli={'jev_model': None, 'config': 'x'})
    assert c.jev_model == 'jev-latest'


def test_default_config_file_absent_is_ok():
    assert load().config_file is None


def test_explicit_config_missing_is_error():
    with pytest.raises(ConfigError, match='file not found'):
        load(config_path=Path('/nope.json'))


def test_config_file_invalid_json(fake_files):
    fake_files[Path('/c.json')] = ValueError('/c.json: invalid JSON: boom')
    with pytest.raises(ConfigError, match='invalid JSON'):
        load(config_path=Path('/c.json'))


def test_config_json_invalid():
    with pytest.raises(ConfigError, match='--config-json: invalid JSON'):
        load(config_json='{nope')


def test_config_json_not_object():
    with pytest.raises(ConfigError, match='must be a JSON object'):
        load(config_json='[1]')


def test_unknown_keys_all_reported():
    with pytest.raises(ConfigError) as e:
        load(config_json='{"foo": 1, "bar": 2}')
    assert '--config-json: unknown key "foo"' in str(e.value)
    assert '--config-json: unknown key "bar"' in str(e.value)


@pytest.mark.parametrize('where', ['file', 'json'])
def test_api_key_rejected(where, fake_files):
    fake_files[CONFIG_DIR / 'config.json'] = {'typesafe_api_key': 'secret-key-123'}
    kwargs = {'config_json': '{"typesafe_api_key": "x"}'} if where == 'json' else {}
    with pytest.raises(ConfigError) as e:
        load(**kwargs)
    assert str(e.value) == cfg.API_KEY_MESSAGE
    assert 'secret-key-123' not in str(e.value)


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        ('pubsub_topic', 'bad'),
        ('pubsub_topic', 'projects/p/subscriptions/s'),
        ('pubsub_subscription', 'projects/p/topics/t'),
    ],
)
def test_bad_resource_names(key, value):
    with pytest.raises(ConfigError, match=key):
        load(config_json=json.dumps({key: value}))


def test_good_resource_names():
    c = load(
        config_json=json.dumps(
            {
                'pubsub_topic': 'projects/p/topics/t',
                'pubsub_subscription': 'projects/p/subscriptions/s',
            }
        )
    )
    assert c.pubsub_topic == 'projects/p/topics/t'
    assert c.pubsub_subscription == 'projects/p/subscriptions/s'


def test_range_errors_collected_together():
    bad = {
        'max_body_chars': 5,
        'max_attempts': 0,
        'pull_max_messages': 101,
        'jev_timeout_seconds': 0,
        'log_level': 'LOUD',
    }
    with pytest.raises(ConfigError) as e:
        load(config_json=json.dumps(bad))
    lines = str(e.value).splitlines()
    assert len(lines) == 5
    assert 'max_body_chars: must be between 100 and 60000 (got 5)' in lines
    assert 'max_attempts: must be at least 1 (got 0)' in lines
    assert any(line.startswith('log_level') for line in lines)
    assert any(line.startswith('jev_timeout_seconds') for line in lines)


def test_type_errors_collected():
    bad = {
        'max_body_chars': 'many',
        'jev_timeout_seconds': True,
        'jev_model': '',
        'max_attempts': 1.5,
        'token_file': 3,
        'gmail_user': None,
    }
    with pytest.raises(ConfigError) as e:
        load(config_json=json.dumps(bad))
    assert len(str(e.value).splitlines()) == 6


def test_env_type_errors_and_float_env():
    with pytest.raises(ConfigError, match='JEV_LABELER_MAX_ATTEMPTS'):
        load(environ={'JEV_LABELER_MAX_ATTEMPTS': 'x'})
    c = load(environ={'JEV_LABELER_JEV_TIMEOUT_SECONDS': '2.5'})
    assert c.jev_timeout_seconds == 2.5


def test_env_null_for_nullable_and_not_nullable():
    c = load(
        config_json='{"pubsub_topic": "projects/p/topics/t"}',
        environ={'JEV_LABELER_PUBSUB_TOPIC': 'null'},
    )
    assert c.pubsub_topic == 'projects/p/topics/t'  # JSON layer wins over env
    c = load(environ={'JEV_LABELER_PUBSUB_TOPIC': 'null'})
    assert c.pubsub_topic is None
    with pytest.raises(ConfigError, match='must not be null'):
        load(environ={'JEV_LABELER_JEV_MODEL': 'null'})


def test_json_null_unsets_nullable():
    c = load(config_json='{"typesafe_api_key_file": null}')
    assert c.typesafe_api_key_file is None


def test_empty_env_value_ignored():
    assert load(environ={'JEV_LABELER_JEV_MODEL': ''}).jev_model == 'jev-latest'


def test_int_valued_float_accepted():
    c = load(config_json='{"jev_timeout_seconds": 5, "max_body_chars": 200.0}')
    assert c.jev_timeout_seconds == 5.0
    assert c.max_body_chars == 200


def test_relative_paths_resolve_by_source(fake_files):
    fake_files[Path('/etc/app/c.json')] = {'token_file': 'tok.json'}
    c = load(config_path=Path('/etc/app/c.json'))
    assert c.token_file == Path('/etc/app/tok.json')
    c = load(cli={'token_file': 'tok.json'})
    assert c.token_file == Path('/cwd/tok.json')
    c = load(environ={'JEV_LABELER_CRITERIA_FILE': 'c.json'})
    assert c.criteria_file == Path('/cwd/c.json')
    c = load(config_json='{"state_db": "s.db"}')
    assert c.state_db == Path('/cwd/s.db')


def test_tilde_expanded(monkeypatch):
    monkeypatch.setenv('HOME', '/home/zed')
    c = load(cli={'token_file': '~/t.json'})
    assert c.token_file == Path('/home/zed/t.json')


def test_explicit_config_path_tilde(monkeypatch, fake_files):
    monkeypatch.setenv('HOME', '/home/zed')
    fake_files[Path('/home/zed/c.json')] = {'jev_model': 'x'}
    assert load(config_path=Path('~/c.json')).jev_model == 'x'


def test_require_message():
    c = load()
    with pytest.raises(ConfigError) as e:
        cfg.require(c, 'pubsub_topic', command='watch start')
    assert str(e.value) == (
        'pubsub_topic is required for `watch start`. Set it in the config file, '
        'with --topic, or JEV_LABELER_PUBSUB_TOPIC.'
    )


def test_require_without_flag_and_satisfied():
    c = load(config_json='{"pubsub_topic": "projects/p/topics/t"}')
    cfg.require(c, 'pubsub_topic', command='x')
    with pytest.raises(ConfigError, match='--typesafe-api-key-file'):
        cfg.require(c, 'typesafe_api_key_file', command='x')


def test_require_key_without_flag(monkeypatch):
    c = load()
    monkeypatch.setitem(cfg._SCHEMA, 'pubsub_topic', ('str', True, None))
    with pytest.raises(ConfigError, match='config file, or JEV_LABELER_PUBSUB_TOPIC'):
        cfg.require(c, 'pubsub_topic', command='x')


def test_resolve_key_from_env():
    assert cfg.resolve_typesafe_api_key(
        load(), {'TYPESAFE_API_KEY': ' k-123456789 '}
    ) == ('k-123456789')


def test_resolve_key_from_file(monkeypatch):
    monkeypatch.setattr(cfg.files, 'read_text', lambda p: 'file-key-123456\n')
    c = load(cli={'typesafe_api_key_file': '/k'})
    assert (
        cfg.resolve_typesafe_api_key(c, {'TYPESAFE_API_KEY': 'env'}) == 'file-key-123456'
    )


def test_resolve_key_file_unreadable(monkeypatch):
    def boom(p):
        raise FileNotFoundError(p)

    monkeypatch.setattr(cfg.files, 'read_text', boom)
    c = load(cli={'typesafe_api_key_file': '/k'})
    with pytest.raises(ConfigError, match='Cannot read typesafe_api_key_file'):
        cfg.resolve_typesafe_api_key(c, {})


def test_resolve_key_missing():
    with pytest.raises(ConfigError, match='Set TYPESAFE_API_KEY'):
        cfg.resolve_typesafe_api_key(load(), {})


def test_redacted_dict_never_has_key():
    c = load()
    d = cfg.redacted_dict(c, {'TYPESAFE_API_KEY': 'super-secret-key'})
    assert d['typesafe_api_key'] == 'set'
    assert 'super-secret-key' not in json.dumps(d)
    assert d['token_file'] == str(CONFIG_DIR / 'token.json')
    assert d['config_file'] is None
    assert cfg.redacted_dict(c, {})['typesafe_api_key'] == 'not set'
