from freecad_ai.config import AppConfig
from freecad_ai.core.dangerous_mode import DangerousMode, get_dangerous_mode


def test_inactive_by_default(monkeypatch):
    monkeypatch.setattr("freecad_ai.config.get_config", lambda: AppConfig())
    assert DangerousMode().active is False


def test_arm_disarm_session(monkeypatch):
    monkeypatch.setattr("freecad_ai.config.get_config", lambda: AppConfig())
    dm = DangerousMode()
    dm.arm()
    assert dm.active is True
    dm.disarm()
    assert dm.active is False


def test_persisted_flag_honored(monkeypatch):
    cfg = AppConfig(dangerous_skip_safety=True)
    monkeypatch.setattr("freecad_ai.config.get_config", lambda: cfg)
    dm = DangerousMode()
    assert dm.active is True          # persisted hand-edit, no session arm
    assert dm.persisted is True


def test_arming_does_not_mutate_config(monkeypatch):
    cfg = AppConfig()
    monkeypatch.setattr("freecad_ai.config.get_config", lambda: cfg)
    dm = DangerousMode()
    dm.arm()
    assert cfg.dangerous_skip_safety is False  # session arm never persists


def test_singleton_identity():
    assert get_dangerous_mode() is get_dangerous_mode()


def test_arm_then_save_config_does_not_persist(tmp_path, monkeypatch):
    import json
    import freecad_ai.config as config_mod
    cfg = config_mod.AppConfig()
    monkeypatch.setattr(config_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(tmp_path / "config.json"))

    from freecad_ai.core.dangerous_mode import DangerousMode
    dm = DangerousMode()
    dm.arm()                        # session-only
    config_mod.save_config(cfg)     # any save (e.g. dock-layout change)

    with open(str(tmp_path / "config.json")) as f:
        reloaded = config_mod.AppConfig.from_dict(json.load(f))
    assert reloaded.dangerous_skip_safety is False
