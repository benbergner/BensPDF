"""Tests for reading a PDF's encryption and permissions.

The cases that matter are the ones the corpus taught: encryption almost never
means a password prompt, and the permission bits are a request rather than a lock.
All 8 encrypted files in a 141 file corpus opened with no password, and 6 of them
declared that text may not be copied while this library's own tools copied it
regardless.
"""

import io
import json
import random
from pathlib import Path

from pypdf import PdfWriter
from pypdf.constants import UserAccessPermissions as Allowed
from pypdf.errors import LimitReachedError

from benspdf import check_access
from benspdf.tools import check_access as check_access_module

#: Everything a PDF can permit. Restrictions are made by clearing bits from this.
EVERYTHING = (
    Allowed.PRINT
    | Allowed.MODIFY
    | Allowed.EXTRACT
    | Allowed.ADD_OR_MODIFY
    | Allowed.FILL_FORM_FIELDS
    | Allowed.EXTRACT_TEXT_AND_GRAPHICS
    | Allowed.ASSEMBLE_DOC
    | Allowed.PRINT_TO_REPRESENTATION
)

ACTIONS = (
    "print",
    "print_high_quality",
    "copy",
    "modify",
    "annotate",
    "fill_forms",
    "assemble",
    "accessibility",
)


def build(
    path,
    user_password=None,
    owner_password="owner-secret",
    denied=None,
    algorithm="RC4-128",
) -> str:
    """Write a PDF, encrypted unless `user_password` and `denied` are both None.

    `user_password=""` is the case that matters most: an encrypted file that opens
    without a prompt and carries only restrictions.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)

    if user_password is not None:
        permissions = EVERYTHING
        for flag in denied or ():
            permissions &= ~flag
        writer.encrypt(
            user_password=user_password,
            owner_password=owner_password,
            permissions_flag=permissions,
            algorithm=algorithm,
        )

    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return str(path)


class TestUnencrypted:
    """The ordinary file: no lock, and therefore no restrictions to declare."""

    def test_permits_everything(self, tmp_path):
        pdf = build(tmp_path / "plain.pdf")

        data = check_access(pdf)

        assert data["success"] is True
        assert data["encrypted"] is False
        assert data["needs_password"] is False
        assert data["restricted"] is False
        assert data["restrictions"] == []
        assert data["encryption"] is None

    def test_every_action_is_allowed_not_unknown(self, tmp_path):
        """A PDF cannot restrict anything without encryption, so True, not null."""
        pdf = build(tmp_path / "plain.pdf")

        permissions = check_access(pdf)["permissions"]

        assert set(permissions) == set(ACTIONS)
        assert all(permissions.values())

    def test_summary_says_so_plainly(self, tmp_path):
        pdf = build(tmp_path / "plain.pdf")

        assert "Not encrypted" in check_access(pdf)["summary"]


class TestEncryptedButOpen:
    """The common real case: encrypted, no user password, restrictions declared.

    Every encrypted file in the corpus was like this. Reporting "encrypted"
    without "opens anyway" would read as a locked door that isn't there.
    """

    def test_opens_without_a_password(self, tmp_path):
        pdf = build(tmp_path / "open.pdf", user_password="", denied=[Allowed.MODIFY])

        data = check_access(pdf)

        assert data["encrypted"] is True
        assert data["needs_password"] is False

    def test_denied_actions_are_listed(self, tmp_path):
        pdf = build(
            tmp_path / "restricted.pdf",
            user_password="",
            denied=[Allowed.EXTRACT, Allowed.MODIFY],
        )

        data = check_access(pdf)

        assert data["permissions"]["copy"] is False
        assert data["permissions"]["modify"] is False
        assert data["permissions"]["print"] is True
        assert set(data["restrictions"]) == {"copy", "modify"}
        assert data["restricted"] is True

    def test_summary_admits_the_bits_are_not_enforced(self, tmp_path):
        """The honesty requirement: a restriction on an open file stops nobody."""
        pdf = build(
            tmp_path / "advisory.pdf", user_password="", denied=[Allowed.EXTRACT]
        )

        summary = check_access(pdf)["summary"]

        assert "opens without a password" in summary
        assert "copying text" in summary
        assert "not a lock" in summary

    def test_an_encrypted_file_with_no_restrictions(self, tmp_path):
        pdf = build(tmp_path / "free.pdf", user_password="")

        data = check_access(pdf)

        assert data["encrypted"] is True
        assert data["restricted"] is False
        assert "restricts nothing" in data["summary"]


class TestPasswordProtected:
    """A file that really is locked. This tool still answers."""

    def test_needs_password_is_reported_not_raised(self, tmp_path):
        pdf = build(tmp_path / "locked.pdf", user_password="letmein")

        data = check_access(pdf)

        assert data["success"] is True, "encrypted is an answer here, not a failure"
        assert data["encrypted"] is True
        assert data["needs_password"] is True

    def test_permissions_are_readable_while_locked(self, tmp_path):
        """The encryption dictionary is not itself encrypted.

        Which is why this verb works where every other one can only report
        failure: /P sits in the clear even when the pages do not.
        """
        pdf = build(
            tmp_path / "locked.pdf", user_password="letmein", denied=[Allowed.PRINT]
        )

        data = check_access(pdf)

        assert data["permissions"]["print"] is False
        assert data["restrictions"] == ["print"]
        assert data["encryption"]["algorithm"] == "RC4 128-bit"

    def test_summary_says_the_pages_cannot_be_read(self, tmp_path):
        pdf = build(tmp_path / "locked.pdf", user_password="letmein")

        summary = check_access(pdf)["summary"]

        assert "needs a password" in summary
        assert "not a lock" not in summary, "nothing to disclaim while it is shut"


class TestEncryptionDetail:
    """Naming the cipher, which /V alone does not determine."""

    def test_rc4(self, tmp_path):
        pdf = build(tmp_path / "rc4.pdf", user_password="", algorithm="RC4-128")

        encryption = check_access(pdf)["encryption"]

        assert encryption["algorithm"] == "RC4 128-bit"
        assert encryption["version"] == 2
        assert encryption["key_bits"] == 128

    def test_aes_128_is_told_apart_from_rc4(self, tmp_path):
        """Both are /V 4; only the crypt filter's method distinguishes them."""
        pdf = build(tmp_path / "aes128.pdf", user_password="", algorithm="AES-128")

        encryption = check_access(pdf)["encryption"]

        assert encryption["algorithm"] == "AES-128"
        assert encryption["version"] == 4

    def test_aes_256(self, tmp_path):
        pdf = build(tmp_path / "aes256.pdf", user_password="", algorithm="AES-256")

        encryption = check_access(pdf)["encryption"]

        assert encryption["algorithm"] == "AES-256"
        assert encryption["version"] == 5
        assert encryption["key_bits"] == 256

    def test_password_hashes_are_not_returned(self, tmp_path):
        """/O and /U verify passwords and have no place in a pasteable result."""
        pdf = build(tmp_path / "hashes.pdf", user_password="letmein")

        blob = json.dumps(check_access(pdf))

        assert "/O" not in blob and "/U" not in blob


class TestPermissionIntegrity:
    """`permissions_valid` must not claim a check that never ran."""

    def test_unknown_for_encryption_without_a_signed_copy(self, tmp_path):
        """pypdf answers True for RC4 meaning "nothing to check"; that is not True."""
        pdf = build(tmp_path / "rc4.pdf", user_password="", algorithm="RC4-128")

        assert check_access(pdf)["permissions_valid"] is None

    def test_verified_for_aes_256(self, tmp_path):
        pdf = build(tmp_path / "aes256.pdf", user_password="", algorithm="AES-256")

        assert check_access(pdf)["permissions_valid"] is True

    def test_unknown_while_locked(self, tmp_path):
        """The check cannot run before the document is decrypted."""
        pdf = build(
            tmp_path / "locked.pdf", user_password="letmein", algorithm="AES-256"
        )

        assert check_access(pdf)["permissions_valid"] is None

    def test_unencrypted_has_nothing_to_verify(self, tmp_path):
        assert check_access(build(tmp_path / "plain.pdf"))["permissions_valid"] is None


class TestFailures:
    """Failures return a reason instead of raising."""

    def test_missing_file(self, tmp_path):
        data = check_access(str(tmp_path / "nope.pdf"))

        assert data["success"] is False
        assert data["file_exists"] is False

    def test_not_a_pdf(self, tmp_path):
        not_pdf = tmp_path / "notes.txt"
        not_pdf.write_text("This is not a PDF")

        assert check_access(str(not_pdf))["success"] is False

    def test_a_pypdf_error_outside_pdfreaderror_is_still_handled(
        self, tmp_path, monkeypatch
    ):
        pdf = build(tmp_path / "limit.pdf")

        def over_the_limit(*args, **kwargs):
            raise LimitReachedError("too many objects")

        monkeypatch.setattr(check_access_module, "PdfReader", over_the_limit)

        data = check_access(pdf)

        assert data["success"] is False
        assert "LimitReachedError" in data["error"]

    def test_an_unreadable_encryption_dictionary_still_answers(
        self, tmp_path, monkeypatch
    ):
        """Encrypted is knowable even when the details are not."""
        pdf = build(tmp_path / "odd.pdf", user_password="")

        monkeypatch.setattr(
            check_access_module,
            "_encryption",
            lambda reader: {
                "algorithm": None,
                "version": None,
                "revision": None,
                "key_bits": None,
                "encrypts_metadata": True,
            },
        )

        data = check_access(pdf)

        assert data["success"] is True
        assert data["encrypted"] is True
        assert "unrecognized cipher" in data["summary"]

    def test_corrupted_files_never_raise(self, tmp_path):
        source = Path(build(tmp_path / "source.pdf", user_password="")).read_bytes()
        rng = random.Random(0)
        target = tmp_path / "mangled.pdf"

        for trial in range(40):
            data = bytearray(source)
            mode = trial % 4
            if mode == 0:
                data = data[: rng.randint(1, len(data))]
            elif mode == 1:
                for _ in range(rng.randint(1, 40)):
                    data[rng.randrange(len(data))] = rng.randrange(256)
            elif mode == 2:
                start = rng.randrange(len(data))
                del data[start : start + rng.randint(1, 800)]
            else:
                data = bytearray(rng.randbytes(rng.randint(1, 500)))

            target.write_bytes(bytes(data))
            result = check_access(str(target))

            assert result["success"] in (True, False)
            if not result["success"]:
                assert result["error"]


class TestJsonSafety:
    """Results cross an MCP boundary, so every value has to serialize."""

    def test_result_is_json_serializable(self, tmp_path):
        pdf = build(
            tmp_path / "json.pdf",
            user_password="",
            denied=[Allowed.PRINT, Allowed.EXTRACT],
            algorithm="AES-256",
        )

        json.dumps(check_access(pdf))  # raises on pypdf objects
