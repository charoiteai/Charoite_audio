"""Public synthetic shell control flow; contains no runtime wrapper source."""


def make_wrapper() -> bytes:
    rows = ['# synthetic source\n'] * 216
    replacements = {
        1: '#!/bin/bash\n', 21: 'set -u\n', 25: 'NIGHTLY="$1"\n', 27: 'STATE="$2"\n',
        41: 'refuse() { exit 75; }\n', 42: 'broken() { exit 2; }\n',
        62: 'in_window() { [ "${FAKE_WINDOW:-yes}" = yes ]; }\n',
        68: 'night_sleep_running() { [ "${FAKE_OTHER_RUNNING:-no}" = yes ]; }\n',
        69: 'ux_step() { printf "ux\\n" >> "$STATE/steps"; }\n',
        71: 'slot_decide() {\n', 72: '  in_window || return 1\n',
        73: '  TODAY="${FAKE_DAY:-2026-01-02}"\n',
        74: '  if [ -e "$STATE/started-$TODAY" ]; then\n',
        75: '    WHY="started"\n', 76: '    return 1\n', 77: '  fi\n',
        78: '  if night_sleep_running; then\n', 79: '    return 1\n',
        80: '  fi\n', 82: '  return 0\n', 83: '}\n',
        169: 'slot_decide || exit 75\n',
        181: '[ "${FAKE_RESOURCE:-yes}" = yes ] || exit 75\n',
        182: 'printf "resource\\n" >> "$STATE/steps"\n',
        183: 'slot_decide || exit 75\n', 196: 'mkdir -p "$STATE"\n',
        197: 'MARK="$STATE/started-$TODAY"\n',
        198: '( set -C; : > "$MARK" ) || exit 75\n',
        199: 'find "$STATE" -maxdepth 1 -name "started-*" >/dev/null\n',
        209: 'ux_step\n', 215: 'exec /bin/bash "$NIGHTLY"\n',
    }
    for number, value in replacements.items():
        rows[number - 1] = value
    return ''.join(rows).encode()
