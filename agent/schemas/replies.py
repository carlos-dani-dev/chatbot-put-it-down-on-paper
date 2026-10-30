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

@dataclass
class CtaUrlReply:
    text: str
    button_text: str
    url: str

@dataclass
class MultiTextReply:
    """Várias mensagens de texto seguidas — ex.: o Pix copia e cola sozinho numa
    mensagem, pra o usuário copiar só o código segurando a mensagem."""
    texts: list[str]

    @property
    def text(self) -> str:
        return "\n\n".join(self.texts)
