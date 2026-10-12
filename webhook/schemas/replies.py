from dataclasses import dataclass


@dataclass
class ListReply:
    text: str
    button_label: str
    rows: list[tuple[str, str]]

@dataclass
class TextReply:
    text:str

@dataclass
class ButtonsReply:
    text: str
    buttons: list[tuple[str, str]]
