"""config.example.yaml — единственная документация по ключам конфига.

Ключ, который читает код, но которого нет в примере, не существует для
пользователя: узнать о нём можно только чтением исходников. Так вышло с
sufler.language — ради него сделаны три релиза интернационализации (0.20.0,
0.21.0, 0.22.0), английские промпты, английские значения полей графа и
демо-граф graph_en, а включить его нельзя, не заглянув в src/llm.py.
Хуже: в примере есть stt.language — ЯЗЫК РАСПОЗНАВАНИЯ, другой ключ в
другой секции. Пользователь ставит его и получает не то.

Тест ловит расхождение целиком, а не один этот случай: собирает ключи
sufler.*, которые код реально читает, и требует, чтобы каждый был либо в
примере, либо в списке исключений ниже — с причиной.
"""
import pathlib
import re
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "config" / "config.example.yaml"
CI = REPO / ".github" / "workflows" / "ci.yml"

# Ключи, которых сознательно нет в примере, — каждый с причиной.
UNDOCUMENTED_ON_PURPOSE = {
    # шаблон минуток показан закомментированным блоком: значение
    # многострочное, «пустой ключ» тут выглядел бы как поломка
    "minutes_template",
    # sufler.model читает только dictate_note.py, всё остальное берёт
    # llm.model. Ключ-дубль: пока не решено, оставлять его или свести к
    # llm.model, рекламировать в примере нечего
    "model",
}

# Все формы чтения sufler.*, живущие в src/. Кавычки — любые: cfg['sufler']
# встречается наравне с cfg["sufler"], и разница в кавычках не должна решать,
# проверяется ключ или нет.
Q = r"[\"']"
READ_PATTERNS = (
    # cfg["sufler"].get("key") · (cfg.get("sufler") or {}).get("key")
    # После имени секции закрывается либо скобка индекса, либо скобка самого
    # cfg.get(...) — обе, иначе вторая форма из комментария не ловится.
    re.compile(rf"{Q}sufler{Q}[\]\)]?\s*(?:or\s*\{{\}}\s*\))?\.get\(\s*{Q}([a-z0-9_]+){Q}"),
    # cfg.get("sufler", {}).get("key")
    re.compile(rf"\.get\({Q}sufler{Q},\s*\{{\}}\)\.get\(\s*{Q}([a-z0-9_]+){Q}"),
    # cfg["sufler"]["key"] — прямое индексирование, падает на отсутствии ключа
    re.compile(rf"\[{Q}sufler{Q}\]\[\s*{Q}([a-z0-9_]+){Q}\s*\]"),
)


def _keys_read_by_code() -> set[str]:
    keys: set[str] = set()
    for path in sorted((REPO / "src").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in READ_PATTERNS:
            keys.update(pattern.findall(text))
    # privacy.py читает свои ключи через переменную — регуляркой не поймать,
    # поэтому список он объявляет сам
    sys.path.insert(0, str(REPO / "src"))
    import privacy
    keys.update(privacy.KEYS)
    return keys


def _example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_example_is_valid_yaml():
    assert isinstance(_example(), dict)


def test_language_key_is_documented():
    """Тот самый ключ, ради которого сделана вся интернационализация."""
    assert "language" in _example()["sufler"], \
        "sufler.language не описан в примере — включить английский можно только из исходников"


def test_stt_language_is_a_different_key():
    """Защита от путаницы: два language в конфиге, и это нормально — но оба
    должны быть на месте, иначе один читается вместо другого."""
    cfg = _example()
    assert "language" in cfg["stt"], "stt.language — язык распознавания"
    assert "language" in cfg["sufler"], "sufler.language — язык документов встречи"


def test_scanner_sees_every_shape_of_read():
    """Регулярка ищет ключи в исходниках — значит она сама нуждается в тесте.

    Ключ, прочитанный формой, которой нет в READ_PATTERNS, не «разрешён» —
    он невидим, и проверка ниже молча его пропускает. Здесь перечислены
    ключи, которые код читает всеми живыми в репозитории формами:
    cfg["sufler"]["role"], cfg['sufler']['hotkey_hint'] (одинарные кавычки),
    (cfg.get("sufler") or {}).get("user_name").
    """
    keys = _keys_read_by_code()
    for key in ("role", "graph_dir", "hotkey_hint", "user_name"):
        assert key in keys, \
            f"сканер не видит чтение sufler.{key} — расширьте READ_PATTERNS"


# Каждая форма — отдельной строкой, а не поиском по src/. Ключ, который
# читают ДВУМЯ формами, скрывает дыру в третьей: user_name читается и как
# cfg["sufler"].get(...), и как (cfg.get("sufler") or {}).get(...), поэтому
# тест выше проходил, даже когда вторую форму регулярка не понимала.
SHAPES = (
    'cfg["sufler"].get("quiet")',
    "cfg['sufler'].get('quiet')",
    '(cfg.get("sufler") or {}).get("quiet")',
    "(cfg.get('sufler') or {}).get('quiet')",
    'cfg.get("sufler", {}).get("quiet")',
    'cfg["sufler"]["quiet"]',
)


def test_scanner_recognises_each_shape_by_itself():
    for src in SHAPES:
        found: set[str] = set()
        for pattern in READ_PATTERNS:
            found.update(pattern.findall(src))
        assert "quiet" in found, f"форма чтения не распознана: {src}"


def test_cloud_switches_stay_under_the_same_guard():
    """Облачные тумблеры читает privacy.py — через переменную, не литерал.

    Регуляркой такое чтение не поймать, и ровно в тот момент, когда решение
    об облаке съехало в один модуль, cloud_live и cloud_enrich выпали
    из-под проверки на документированность. Список ключей privacy.py
    экспортирует сам — так он не разъедется с этим тестом.
    """
    assert {"cloud_live", "cloud_enrich"} <= _keys_read_by_code(), \
        "ключи облака не попали в список читаемых — их отсутствие в примере не поймают"


def test_every_key_the_code_reads_is_documented():
    missing = sorted(_keys_read_by_code() - set(_example()["sufler"]) - UNDOCUMENTED_ON_PURPOSE)
    assert not missing, (
        "код читает ключи, которых нет в config.example.yaml: "
        + ", ".join(f"sufler.{k}" for k in missing)
        + " — опишите их в примере или внесите в UNDOCUMENTED_ON_PURPOSE с причиной")


def test_ci_checks_the_same_required_keys():
    """CI проверяет обязательные ключи своим списком — он не должен отставать."""
    text = CI.read_text(encoding="utf-8")
    for path in ("sufler.user_name", "sufler.language"):
        assert f'"{path}"' in text, f"ci.yml не проверяет {path}"


def test_every_preset_starts():
    """Все три пресета обязаны быть запускаемыми, а не только русский.

    Релиз 0.34.0 добавил английское и китайское «лицо» продукта, README велит
    `cp config/config.example.zh.yaml config/config.yaml` — а в пресете стоял
    stt.backend: whisper, которого STT не знал: оба новых пресета падали
    ValueError на первой же строке. Тест читал только русский пример и этого
    не видел; CI проверял наличие ключей, но не допустимость значений.
    """
    backends = _known_backends()
    for preset in sorted((REPO / "config").glob("config.example*.yaml")):
        cfg = yaml.safe_load(preset.read_text(encoding="utf-8"))
        backend = cfg["stt"]["backend"]
        assert backend in backends, (
            f"{preset.name}: stt.backend={backend!r} не принимается src/stt.py "
            f"(известны: {sorted(backends)}) — пресет не запустится")
        # у выбранного бэкенда должно быть чем грузить модель
        need = {"gigaam": "gigaam_model", "parakeet": "parakeet_model",
                "whisper": "whisper_model", "mlx_whisper": "whisper_model"}[backend]
        assert cfg["stt"].get(need), f"{preset.name}: нет stt.{need} для backend={backend}"


def _known_backends() -> set[str]:
    """Имена бэкендов вытаскиваем из самого кода, чтобы список не разъезжался."""
    src = (REPO / "src" / "stt.py").read_text(encoding="utf-8")
    found = set(re.findall(r'self\.backend\s*==\s*"([a-z_]+)"', src))
    found |= set(re.findall(r'self\.backend\s+in\s+\(([^)]+)\)', src)) and set(
        re.findall(r'"([a-z_]+)"', "".join(re.findall(r'self\.backend\s+in\s+\(([^)]+)\)', src))))
    return found
