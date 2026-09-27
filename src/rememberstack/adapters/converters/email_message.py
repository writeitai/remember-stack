"""The D138 ``email`` route: one ``.eml`` message → prose.

Python's ``email`` package parses the message. ``document.md`` holds the
subject as its heading, the From/To/Cc/Date headers, the body (the plain-text
part, or the HTML part converted with markitdown) and the attachments listed
by name and size; attachments are not converted. D134 metadata: title =
Subject, authors = From, recipients = To, Cc and Bcc, created_at = Date,
thread_ref = the root of References, else the message's own Message-ID.
"""

from datetime import timezone
import email
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import getaddresses
from email.utils import parsedate_to_datetime
import io
from typing import Final

from markitdown import MarkItDown
from markitdown import StreamInfo
from markitdown._exceptions import MarkItDownException
from markitdown.converters import HtmlConverter

from rememberstack.adapters.converters.time_limit import run_with_time_limit
from rememberstack.core import entire_document_labeling
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import ManifestComponent
from rememberstack.model.document_metadata import DocumentMetadata
from rememberstack.model.document_metadata import DocumentPerson

EMAIL_CONVERTER_VERSION: Final = "email-2026.09"
"""Pins the email route: header lines, body choice, attachment list, metadata."""

_SHOWN_HEADERS: Final = ("From", "To", "Cc", "Date")


class EmailConverter:
    """Read one RFC 822 message: headers, body and the attachment list."""

    def __init__(self) -> None:
        """Build the markitdown instance used for HTML bodies once."""
        self._markitdown = MarkItDown(enable_builtins=False, enable_plugins=False)
        self._markitdown.register_converter(HtmlConverter())

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "email"

    @property
    def version(self) -> str:
        """The pinned email route version (D38)."""
        return EMAIL_CONVERTER_VERSION

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """Convert one message; bytes without any message header fail."""
        return run_with_time_limit(
            work=lambda: self._convert(content=content), what="email conversion"
        )

    def _convert(self, *, content: bytes) -> ConversionResult:
        """Parse the message and render it as Markdown."""
        message = email.message_from_bytes(content, policy=default_policy)
        if not isinstance(message, EmailMessage) or not any(
            message[header] for header in ("From", "To", "Subject", "Date")
        ):
            raise ConversionError(
                "the file is not an email message (no From, To, Subject or Date)"
            )
        subject = _header(message=message, name="Subject")
        lines = [f"# {subject or '(no subject)'}", ""]
        lines.extend(
            f"- {name}: {value}"
            for name in _SHOWN_HEADERS
            if (value := _header(message=message, name=name))
        )
        parts = ["\n".join(lines)]
        body = self._body(message=message)
        if body:
            parts.append(body)
        attachments = _attachment_lines(message=message)
        if attachments:
            parts.append("## Attachments\n\n" + "\n".join(attachments))
        document_md = "\n\n".join(parts) + "\n"
        return ConversionResult(
            document_md=document_md,
            metadata=_metadata(message=message, subject=subject),
            manifest=ConverterManifest(
                components=(
                    ManifestComponent(
                        name="email",
                        version=EMAIL_CONVERTER_VERSION,
                        execution="library-local",
                    ),
                ),
                coverage=(
                    ConversionCoverage(
                        policy="email-body",
                        complete=False,
                        gaps=("attachments are listed, not converted",),
                    )
                    if attachments
                    else ConversionCoverage(policy="email-body", complete=True)
                ),
                derivation_ranges=entire_document_labeling(
                    document_md=document_md,
                    derivation_kind="prose",
                    evidence_mode="source_expression",
                ),
            ),
        )

    def _body(self, *, message: EmailMessage) -> str:
        """The plain-text body, else the HTML body converted to Markdown."""
        part = message.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        try:
            text = part.get_content()
        except (LookupError, UnicodeDecodeError) as err:
            raise ConversionError("the email body could not be decoded") from err
        if not isinstance(text, str):
            return ""
        if part.get_content_subtype() == "html":
            try:
                text = self._markitdown.convert_stream(
                    io.BytesIO(text.encode("utf-8")),
                    stream_info=StreamInfo(mimetype="text/html", charset="utf-8"),
                ).text_content
            except MarkItDownException as err:
                raise ConversionError(
                    "the email's HTML body could not be read"
                ) from err
        return text.strip()


def _header(*, message: EmailMessage, name: str) -> str:
    """One header's decoded value on a single line; empty when absent."""
    value = message.get(name)
    return " ".join(str(value).split()) if value is not None else ""


def _attachment_lines(*, message: EmailMessage) -> list[str]:
    """One line per attachment: its file name and decoded size."""
    lines: list[str] = []
    for part in message.iter_attachments():
        payload = part.get_payload(decode=True)
        size = len(payload) if isinstance(payload, bytes) else len(part.as_bytes())
        lines.append(f"- {part.get_filename() or 'unnamed'} ({size:,} bytes)")
    return lines


def _metadata(*, message: EmailMessage, subject: str) -> DocumentMetadata:
    """D134 metadata from the headers (values as declared, not verified)."""
    created_at = None
    date = _header(message=message, name="Date")
    if date:
        try:
            parsed = parsedate_to_datetime(date)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            created_at = parsed.astimezone(timezone.utc)
    references = _header(message=message, name="References").split()
    thread_root = (
        references[0] if references else _header(message=message, name="Message-ID")
    )
    return DocumentMetadata(
        title=subject or None,
        authors=_people(message=message, names=("From",)),
        recipients=_people(message=message, names=("To", "Cc", "Bcc")),
        created_at=created_at,
        thread_ref=thread_root or None,
    )


def _people(
    *, message: EmailMessage, names: tuple[str, ...]
) -> tuple[DocumentPerson, ...]:
    """The people in address headers, skipping entries naming nobody."""
    values = [str(value) for name in names for value in message.get_all(name, [])]
    return tuple(
        DocumentPerson(name=display.strip() or None, address=address.strip() or None)
        for display, address in getaddresses(values)
        if display.strip() or address.strip()
    )
