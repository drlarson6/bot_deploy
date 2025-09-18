# session_state.py
from enum import Enum, auto

class SessionType(Enum):
    NONE = auto()
    DEVIATION = auto()
    SPREADSHEET = auto()

class SessionState:
    def __init__(self):
        self.mode = False              # True while a GPT-driven session is active
        self.type = SessionType.NONE   # Which kind of session
        self.id = None                 # Current session id (uuid hex)

session = SessionState()               # shared singleton
