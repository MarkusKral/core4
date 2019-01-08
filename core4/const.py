"""
This module carries several global constants relevant to multiple core4
modules.
"""

CORE4 = "core4"
PREFIX = "project"  # project logger beneath "core4.project"
COP = "cop"
CORE4_API = "/core4/api/v1"
INFO_URL = CORE4_API + "/info"
ENTER_URL = CORE4_API + "/enter"
CARD_URL = INFO_URL + "/card"
HELP_URL = INFO_URL
FILE_URL = CORE4_API + "/file"
CARD_METHOD = "XCARD"
HELP_METHOD = "XHELP"

VENV = ".venv"
REPOSITORY = ".repos"
VENV_PYTHON = VENV + "/bin/python"
