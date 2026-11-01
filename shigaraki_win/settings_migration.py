"""One explicit a1 -> a2 Control default migration; later choices are retained."""
CONTROL_MIGRATION = "control_default_v2"


def migrate_control_default(settings):
    if settings.get(CONTROL_MIGRATION) is True:
        return False
    settings["modifier"] = 0
    settings[CONTROL_MIGRATION] = True
    return True
