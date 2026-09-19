"""Persistent, local-operator control. No remote administration endpoint."""
from pathlib import Path
from .config import CONFIG


class AutomationControl:
    def __init__(self, path=None):
        self.path = Path(path or CONFIG.kill_switch_path)

    @property
    def disabled(self):
        try:
            self.path.stat()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return True  # An unreadable control must fail closed.

    def disable(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def enable(self):
        self.path.unlink(missing_ok=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["disable", "enable", "status"])
    args = parser.parse_args()
    control = AutomationControl()
    if args.action != "status":
        getattr(control, args.action)()
    print("Automatic replies: " + ("PAUSED" if control.disabled else "ENABLED"))


if __name__ == "__main__":
    main()
