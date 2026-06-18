"""Pinned 0-LLM reads: find_pin locates the unique element holding a scalar value; read_pin reads
its live text deterministically. No LLM, no network."""

from __future__ import annotations

from ultracua.browser import BrowserSession
from ultracua.pin import _parse, find_pin, read_pin


def test_parse_is_strict_no_wrong_value_no_crash() -> None:
    # clean / thousands-grouped scalars parse
    assert _parse("1,234 items", "int") == 1234
    assert _parse("42", "int") == 42
    assert _parse("-5", "int") == -5
    assert _parse("$1,234.56", "float") == 1234.56
    assert _parse("Active", "str") == "Active"
    # multiple numbers / ranges / dates / sci-notation / locale -> None (fail loud; never a wrong value)
    for bad in ("total 1,000 tax 200", "-5 was 3", "5-10", "2024-01-15", "--", ""):
        assert _parse(bad, "int") is None      # and crucially: no uncaught ValueError
    for bad in ("1e3", "1.5e6", "12,5", "p 1.5 q 2.5"):
        assert _parse(bad, "float") is None


async def test_find_and_read_pin_roundtrip_int() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content("<h1>Report</h1><p id='ans'>1234</p>")
        pin = await find_pin(session.page, 1234)
        assert pin is not None and pin["value_type"] == "int"
        await session.page.set_content("<h1>Report</h1><p id='ans'>5678</p>")  # value changes
        assert await read_pin(session.page, pin) == 5678                        # reads the LIVE value
    finally:
        await session.close()


async def test_find_pin_string_via_testid() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content("<div><span data-testid='st'>Active</span></div>")
        pin = await find_pin(session.page, "Active")
        assert pin is not None and pin["value_type"] == "str" and pin["locator"]["testid"] == "st"
        assert await read_pin(session.page, pin) == "Active"
    finally:
        await session.close()


async def test_find_pin_skips_ambiguous_and_nonscalar() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content("<p>42</p><span>42</span>")  # two elements hold '42'
        assert await find_pin(session.page, 42) is None              # ambiguous -> no pin
        await session.page.set_content("<p id='x'>7</p>")
        assert await find_pin(session.page, {"a": 1}) is None        # non-scalar
        assert await find_pin(session.page, True) is None            # bool excluded
    finally:
        await session.close()


async def test_read_pin_returns_none_when_element_gone() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content("<p id='ans'>1</p>")
        pin = await find_pin(session.page, 1)
        await session.page.set_content("<h1>gone</h1>")              # the pinned element no longer exists
        assert await read_pin(session.page, pin) is None
    finally:
        await session.close()


async def test_find_pin_refuses_positional_only_anchor() -> None:
    # a value in a plain element with NO id/test-id has only a positional css path; after a layout
    # shift that path could resolve to a different element -> refuse to pin (use the LLM extractor).
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content("<div><span>Active</span></div>")
        assert await find_pin(session.page, "Active") is None
    finally:
        await session.close()


async def test_read_pin_fails_loud_on_ambiguous_resolve() -> None:
    # the pinned anchor resolves to 2+ elements on replay -> None (fail loud), never a blind .first
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content("<div data-testid='m'>42</div>")
        pin = await find_pin(session.page, 42)
        assert pin is not None
        await session.page.set_content("<div data-testid='m'>7</div><div data-testid='m'>9</div>")
        assert await read_pin(session.page, pin) is None
    finally:
        await session.close()
