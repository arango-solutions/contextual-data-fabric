"""The demo page is a server-rendered string template (deploy/demo/server.py).

Importing the module boots FederationService.from_env(), which needs the live
stack — so these tests assert on the template source itself: the invariants
the browser UI depends on, and the suggestion-dropdown contract (prepared
questions pop up on focus instead of permanently occupying the page).
"""

from pathlib import Path

import pytest

PAGE_SOURCE = (Path(__file__).parent.parent / "deploy" / "demo" / "server.py").read_text()
EDITOR_SOURCE = (Path(__file__).parent.parent / "deploy" / "demo" / "editor.js").read_text()


def test_page_still_injects_questions_and_sparql() -> None:
    assert "__QUESTIONS__" in PAGE_SOURCE
    assert "__SPARQL__" in PAGE_SOURCE


def test_suggestions_are_a_dropdown_not_inline_chips() -> None:
    # The dropdown is anchored to the input and hidden until focus.
    assert 'id="suggest"' in PAGE_SOURCE
    assert 'role="combobox"' in PAGE_SOURCE
    assert 'role="listbox"' in PAGE_SOURCE
    assert 'id="clear-query"' in PAGE_SOURCE
    assert 'onclick="clearQuestion()"' in PAGE_SOURCE
    # The old always-visible chip row must not come back.
    assert 'id="examples"' not in PAGE_SOURCE
    assert '"chip"' not in PAGE_SOURCE


@pytest.mark.parametrize(
    "wiring",
    [
        "addEventListener('focus', openSuggestions)",
        "addEventListener('input', openSuggestions)",
        "addEventListener('blur', closeSuggestions)",
        "function clearQuestion()",
        "input.value = '';",
        "input.focus();",
        # mousedown (not click) so selection wins the race against the
        # input's blur closing the list — the bug this UI pattern invites.
        "item.onmousedown",
        "e.preventDefault(); pickSuggestion(q);",
    ],
)
def test_dropdown_wiring_present(wiring: str) -> None:
    assert wiring in PAGE_SOURCE


@pytest.mark.parametrize("key", ["ArrowDown", "ArrowUp", "Escape"])
def test_keyboard_navigation_handled(key: str) -> None:
    assert f"e.key === '{key}'" in PAGE_SOURCE


def test_ask_window_renders_llm_metrics() -> None:
    assert 'id="metrics"' in PAGE_SOURCE
    assert "renderMetrics(d.nl_metrics, d.execution_metrics)" in PAGE_SOURCE
    assert "LLM compute time" in PAGE_SOURCE
    assert "prompt_tokens" in PAGE_SOURCE
    assert "completion_tokens" in PAGE_SOURCE
    assert "cost_usd" in PAGE_SOURCE


def test_metrics_separate_plan_wall_time_from_source_execution() -> None:
    assert "plan wall time" in PAGE_SOURCE
    assert "execution.total_duration_ms" in PAGE_SOURCE
    assert "execution.legs" in PAGE_SOURCE
    assert "leg.duration_ms" in PAGE_SOURCE


def test_provenance_panel_renders_actual_execution_workflow() -> None:
    assert "Provenance &amp; Execution" in PAGE_SOURCE
    assert 'aria-label="Query provenance workflow"' in PAGE_SOURCE
    for step in ("Conceptual query", "Federate", "Join", "Grounded answer"):
        assert step in PAGE_SOURCE
    assert "legs.map(s =>" in PAGE_SOURCE
    assert "srcTag(s.kind, s.source_id)" in PAGE_SOURCE
    assert "s.row_count" in PAGE_SOURCE


def test_conceptual_query_editor_is_wired_into_the_page() -> None:
    # The editor script and the live-catalog vocabulary are injected at render
    # time; the textarea keeps its id so run() reads .value unchanged.
    assert "__EDITOR_JS__" in PAGE_SOURCE
    assert "initSparqlEditor('q', __VOCAB__);" in PAGE_SOURCE
    assert 'replace("__EDITOR_JS__", EDITOR_JS)' in PAGE_SOURCE
    assert '"classes": vocab' in PAGE_SOURCE
    assert '<textarea id="q"' in PAGE_SOURCE


def test_editor_completion_is_catalog_scoped_and_syntax_directed() -> None:
    assert "function initSparqlEditor" in EDITOR_SOURCE
    assert "vocab.classes" in EDITOR_SOURCE
    # `?x a c:▮` completes classes only; a typed subject scopes its properties.
    assert "afterA" in EDITOR_SOURCE
    assert "subjectClass(" in EDITOR_SOURCE
    # A concept no source maps is painted as an error — the refuse-over-guess
    # contract, visible while typing.
    assert "sqed-unknown" in EDITOR_SOURCE


def test_editor_understands_full_iri_concept_spelling() -> None:
    # <urn:…:concept#Name> tokenizes, tints, and completes like c:Name; the
    # base comes from the injected vocabulary, not a hardcoded string.
    assert "vocab.base" in EDITOR_SOURCE
    assert "const iriForm" in EDITOR_SOURCE
    assert "'<' + BASE + name + '>'" in EDITOR_SOURCE
    assert '"base": _SERVICE.catalog.concept_base' in PAGE_SOURCE


def test_editor_pairs_braces_and_quotes() -> None:
    assert "function handlePairing" in EDITOR_SOURCE
    assert "function braceMatch" in EDITOR_SOURCE
    # auto-close, skip-over, wrap-selection, and backspace-removes-both
    assert "setRangeText(e.key + PAIR[e.key]" in EDITOR_SOURCE
    assert "skip over" in EDITOR_SOURCE
    assert "'sqed-match'" in EDITOR_SOURCE and "'sqed-unmatch'" in EDITOR_SOURCE
    assert ".sqed-match" in PAGE_SOURCE and ".sqed-unmatch" in PAGE_SOURCE


def test_variable_suggestions_filter_on_the_typed_prefix() -> None:
    # `?nam` must offer only the query's variables starting with `?nam` —
    # every other candidate kind already filtered lexically; vars must too.
    assert ".filter((v) => v.startsWith(tok.text))" in EDITOR_SOURCE


def test_editor_quote_handling_is_parity_aware() -> None:
    # Inside an open string a quote CLOSES it — exactly one, never a pair
    # (typing the closer back into `"signal ;` must not yield `"signal"" ;`).
    assert "function inString" in EDITOR_SOURCE
    assert "e.key === '\"' && inString(v, s)" in EDITOR_SOURCE
    # The quote pair at the caret is marked like braces are.
    assert "function scanQuote" in EDITOR_SOURCE
    # An unterminated string is painted red to the end of the line, not silent.
    assert "sqed-open-str" in EDITOR_SOURCE
    assert ".sqed-open-str" in PAGE_SOURCE


def test_editor_offers_the_namespace_and_never_doubles_the_closing_angle() -> None:
    # Deleting the '#' inside <urn:…:concept#> must offer the bare namespace
    # back (exclusively, on a PREFIX line) …
    assert "'namespace · PREFIX base'" in EDITOR_SOURCE
    # … and accepting an IRI completion just before an existing '>' consumes
    # it instead of producing '>>'.
    assert "it.insert.endsWith('>') && ta.value[end] === '>'" in EDITOR_SOURCE


def test_generated_sparql_is_formatted_on_fill() -> None:
    assert "function formatSparql" in EDITOR_SOURCE
    assert "function setSparqlEditorValue" in EDITOR_SOURCE
    # the ask flow fills the editor through the formatter, and there is a
    # manual Format button for hand-pasted queries
    assert "setSparqlEditorValue('q', d.conceptual_sparql);" in PAGE_SOURCE
    assert 'id="fmt"' in PAGE_SOURCE


@pytest.mark.parametrize(
    "wiring",
    [
        "e.key === 'ArrowDown'",
        "e.key === 'ArrowUp'",
        "e.key === 'Tab' || e.key === 'Enter'",
        "e.key === 'Escape'",
        # mousedown beats the textarea's blur, same pattern as the ask dropdown
        "b.onmousedown",
    ],
)
def test_editor_keyboard_and_mouse_contract(wiring: str) -> None:
    assert wiring in EDITOR_SOURCE


def test_provenance_labels_and_renders_generated_postgresql_sql() -> None:
    assert "generated ${nativeLanguage(c.kind)}" in PAGE_SOURCE
    assert "kind === 'postgresql'" in PAGE_SOURCE
    assert "native query unavailable for this source" in PAGE_SOURCE
    assert "c.native_query" in PAGE_SOURCE
