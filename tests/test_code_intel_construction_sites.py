"""Code Intelligence live regression (slice 2 live closure): the
construction-site channel.

Live ci-20 task httpx@7c0cda15 "Improve InvalidURL error message.": the goal
names the exception type, so its (trivial) ``__init__`` was scoped first and
the member that composes the messages - ``urlparse``, which raises it - ranked
10th on similarity alone, outside the top five the Developer is grounded in.
A type the query names now also credits the callables that construct it.
The fixture keeps the measured shape (a thin exception class, one long
function raising it with its messages, short decoys sharing the goal's words)
without the benchmark repository.
"""
import textwrap

import pytest

from kriya.code_intel import locate as loc
from kriya.code_intel.service import CodeIntelligenceService

EXCEPTIONS = textwrap.dedent('''\
    class HTTPError(Exception):
        def __init__(self, message):
            super().__init__(message)


    class InvalidURL(Exception):
        def __init__(self, message):
            super().__init__(message)
    ''')

URLPARSE = textwrap.dedent('''\
    from ._exceptions import InvalidURL

    MAX_URL_LENGTH = 65536


    def urlparse(url="", **kwargs):
        if len(url) > MAX_URL_LENGTH:
            raise InvalidURL("URL too long")
        if any(char.isascii() and not char.isprintable() for char in url):
            raise InvalidURL("Invalid non-printable ASCII character in URL")
        for key, value in kwargs.items():
            if value is not None and len(value) > MAX_URL_LENGTH:
                raise InvalidURL(f"URL component '{key}' too long")
        scheme, _, rest = url.partition(":")
        authority, _, path = rest.lstrip("/").partition("/")
        host, _, port = authority.partition(":")
        return scheme, host, port, path


    def describe(exc):
        if isinstance(exc, InvalidURL):
            return "invalid"
        return "other"
    ''')

DECOYS = textwrap.dedent('''\
    def error_message(error):
        return f"error message: {error}"


    def invalid_url_message(url):
        return "invalid url " + url


    def url_error(url, error):
        return url + error


    def message_for_invalid(message):
        return "invalid " + message


    def format_error_message(message, error):
        return message.format(error=error)


    def improve_message(message):
        return message.strip()


    def handle(url):
        try:
            return url.lower()
        except InvalidURL as error:
            return str(error)
    ''')

TEST_FILE = textwrap.dedent('''\
    from httpx._exceptions import InvalidURL


    def test_invalid_url_message():
        error = InvalidURL("x")
        assert str(error) == "x"
    ''')

PATHS = {"httpx/_exceptions.py": EXCEPTIONS, "httpx/_urlparse.py": URLPARSE, "httpx/_messages.py": DECOYS,
         "tests/test_url.py": TEST_FILE}
GOAL = "Improve InvalidURL error message."
GOLD = "httpx._urlparse.urlparse"


@pytest.fixture
def service(tmp_path):
    root = tmp_path / "ws"
    for rel, text in PATHS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    svc = CodeIntelligenceService(str(root), str(tmp_path / "index.db"))
    svc.refresh(list(PATHS))
    yield svc
    svc.close()


def _keys(hits, k=5):
    return [h.lookup_key for h in hits[:k]]


def test_the_member_that_constructs_a_named_type_reaches_the_top_five(service):
    hits = service.locate(GOAL, limit=12)
    assert GOLD in _keys(hits), _keys(hits, 12)
    gold = next(h for h in hits if h.lookup_key == GOLD)
    assert dict(gold.channels)["constructs"] > 0


def test_without_the_channel_the_measured_miss_returns(service, monkeypatch):
    """Negative control: the fixture reproduces the live miss when the
    construction-site channel is removed."""
    monkeypatch.setattr(CodeIntelligenceService, "_credit_construction_sites", lambda *a, **k: None)
    assert GOLD not in _keys(service.locate(GOAL, limit=12))


def test_references_that_do_not_construct_earn_nothing(service):
    hits = {h.lookup_key: dict(h.channels) for h in service.locate(GOAL, limit=50)}
    # isinstance / except / the class line itself are references, not construction
    for key in ("httpx._urlparse.describe", "httpx._messages.handle"):
        assert "constructs" not in hits.get(key, {})
    # the type's own members are its scope (owner_named), never its construction sites
    assert "constructs" not in hits["httpx._exceptions.InvalidURL.__init__"]


def test_test_code_constructing_the_type_is_discounted_unless_the_goal_is_about_tests(service):
    def weight(text):
        hits = {h.lookup_key: dict(h.channels) for h in service.locate(text, limit=50)}
        return hits["tests.test_url.test_invalid_url_message"]["constructs"]

    sites = 2  # urlparse, the test
    assert weight(GOAL) == pytest.approx(loc.CONSTRUCTION_SITE / sites * loc.TEST_CODE_FACTOR)
    assert weight("InvalidURL test asserts the message") == pytest.approx(loc.CONSTRUCTION_SITE / sites)


def test_a_widely_constructed_type_is_not_specific_evidence(tmp_path):
    root = tmp_path / "ws"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg/model.py").write_text("class Point:\n    pass\n")
    body = "\n\n".join(f"def make_{i}():\n    return Point()\n" for i in range(loc.MAX_SIMPLE_MATCHES + 1))
    (root / "pkg/factories.py").write_text("from .model import Point\n\n\n" + body)
    svc = CodeIntelligenceService(str(root), str(tmp_path / "index.db"))
    try:
        svc.refresh(["pkg/model.py", "pkg/factories.py"])
        assert not any("constructs" in dict(h.channels) for h in svc.locate("Point is wrong", limit=50))
    finally:
        svc.close()


def test_java_new_sites_the_innermost_callable_and_current_bytes(tmp_path):
    root = tmp_path / "ws"
    src = root / "src/main/java/shop"
    src.mkdir(parents=True)
    (src / "Receipt.java").write_text("package shop;\npublic class Receipt {\n    Receipt(long c) {}\n"
                                      "    static Receipt of(long c) { return new Receipt(c); }\n}\n")
    (src / "Till.java").write_text(textwrap.dedent("""\
        package shop;
        public class Till {
            Receipt close(long cents) {
                Runnable r = new Runnable() {
                    public void run() { log(); }
                };
                return new Receipt(cents);
            }
            Receipt lookup(long id) { return null; }
            void log() {}
        }
        """))
    paths = ["src/main/java/shop/Receipt.java", "src/main/java/shop/Till.java"]
    svc = CodeIntelligenceService(str(root), str(tmp_path / "index.db"))
    try:
        svc.refresh(paths)
        hits = {h.lookup_key: dict(h.channels) for h in svc.locate("Receipt shows the wrong total", limit=50)}
        assert hits["shop.Till.close"]["constructs"] == pytest.approx(loc.CONSTRUCTION_SITE)
        assert "constructs" not in hits.get("shop.Till.lookup", {})  # mentions, never constructs
        # the type's own factory is its scope (owner_named), not one of its construction sites
        assert "constructs" not in hits["shop.Receipt.of"] and "owner_named" in hits["shop.Receipt.of"]
        # a qualified mention names the type exactly as a simple one does
        qualified = {h.lookup_key: dict(h.channels) for h in svc.locate("shop.Receipt total is wrong", limit=50)}
        assert qualified["shop.Till.close"]["constructs"] == pytest.approx(loc.CONSTRUCTION_SITE)
        # The CURRENT bytes decide: a candidate overlay that moves the construction is seen.
        moved = (src / "Till.java").read_text().replace("return new Receipt(cents);", "return null;").replace(
            "Receipt lookup(long id) { return null; }", "Receipt lookup(long id) { return new Receipt(id); }")
        overlay = svc.with_overlay({"src/main/java/shop/Till.java": moved.encode()})
        hits = {h.lookup_key: dict(h.channels) for h in overlay.locate("Receipt shows the wrong total", limit=50)}
        assert "constructs" in hits["shop.Till.lookup"] and "constructs" not in hits.get("shop.Till.close", {})
    finally:
        svc.close()


@pytest.mark.parametrize("language, body, expected", [
    ("python", 'raise InvalidURL("x")', True),
    ("python", "raise InvalidURL", True),
    ("python", "err = exceptions.InvalidURL(msg)", True),
    ("python", "except InvalidURL as exc:", False),
    ("python", "isinstance(exc, InvalidURL)", False),
    ("python", "class InvalidURL(Exception):", False),
    ("python", "def f() -> InvalidURL:", False),
    ("python", "InvalidURLs(x)", False),
    ("java", "return new Receipt(c);", True),
    ("java", "new shop.Receipt<>()", True),
    ("java", "Receipt r = find();", False),
    ("java", "new ReceiptBuilder()", False),
])
def test_construction_detection(language, body, expected):
    name = "InvalidURL" if language == "python" else "Receipt"
    assert loc.constructs(language, name, body) is expected


def test_a_qualified_mention_names_the_type_when_its_simple_name_is_ambiguous(tmp_path):
    root = tmp_path / "ws"
    (root / "pkg").mkdir(parents=True)
    paths = []
    for i in range(loc.MAX_SIMPLE_MATCHES + 1):  # the simple name alone is not specific evidence
        (root / f"pkg/m{i}.py").write_text("class Receipt:\n    pass\n")
        paths.append(f"pkg/m{i}.py")
    (root / "pkg/till.py").write_text("from . import m0\n\n\ndef close():\n    return m0.Receipt()\n")
    paths.append("pkg/till.py")
    svc = CodeIntelligenceService(str(root), str(tmp_path / "index.db"))
    try:
        svc.refresh(paths)
        assert "constructs" not in dict(next((h.channels for h in svc.locate("Receipt is wrong", limit=50)
                                              if h.lookup_key == "pkg.till.close"), ()))
        hits = {h.lookup_key: dict(h.channels) for h in svc.locate("pkg.m0.Receipt is wrong", limit=50)}
        assert hits["pkg.till.close"]["constructs"] == pytest.approx(loc.CONSTRUCTION_SITE)
    finally:
        svc.close()
