"""Tests for :class:`bsvibe_cli_base.output.OutputFormatter`.

The formatter renders structured Python data (typically a list of dicts
or a single dict) in one of four formats: ``json``, ``yaml``, ``tsv``,
or ``table`` (rich-rendered). Format selection rules:

* Explicit ``--output`` flag wins.
* Otherwise: TTY → ``table``, non-TTY → ``json``. This matches the
  ``ls --color=auto`` convention so piping to ``jq`` keeps machine
  output without extra flags.
* Unknown format names raise ``ValueError`` immediately.

Round-trips matter: ``json`` and ``yaml`` outputs MUST parse back to the
same Python object. The table renderer doesn't have to round-trip — its
job is human readability — so we only assert key fields appear.
"""

from __future__ import annotations

import io
import json

import pytest
import yaml

from bsvibe_cli_base.output import OutputFormatter

_SAMPLE_LIST: list[dict[str, str | int]] = [
    {"name": "alice", "tenant": "acme", "count": 7},
    {"name": "bob", "tenant": "acme", "count": 3},
]
_SAMPLE_DICT: dict[str, str | int] = {"status": "ok", "items": 42}


class TestFormatSelection:
    def test_explicit_flag_overrides_tty(self) -> None:
        f = OutputFormatter(format="json", is_tty=True)
        assert f.format == "json"

    def test_tty_default_is_table(self) -> None:
        f = OutputFormatter(format=None, is_tty=True)
        assert f.format == "table"

    def test_non_tty_default_is_json(self) -> None:
        f = OutputFormatter(format=None, is_tty=False)
        assert f.format == "json"

    def test_unknown_format_rejected(self) -> None:
        with pytest.raises(ValueError, match="output"):
            OutputFormatter(format="xml", is_tty=False)

    def test_format_selection_case_insensitive(self) -> None:
        f = OutputFormatter(format="JSON", is_tty=True)
        assert f.format == "json"


class TestJsonRender:
    def test_list_round_trips(self) -> None:
        f = OutputFormatter(format="json", is_tty=False)
        out = f.render(_SAMPLE_LIST)
        assert json.loads(out) == _SAMPLE_LIST

    def test_dict_round_trips(self) -> None:
        f = OutputFormatter(format="json", is_tty=False)
        out = f.render(_SAMPLE_DICT)
        assert json.loads(out) == _SAMPLE_DICT

    def test_indented(self) -> None:
        f = OutputFormatter(format="json", is_tty=False)
        out = f.render(_SAMPLE_DICT)
        assert "\n" in out  # indent forces multiline

    def test_non_jsonable_falls_back_to_str(self) -> None:
        from pathlib import Path

        f = OutputFormatter(format="json", is_tty=False)
        out = f.render({"path": Path("/tmp/x")})
        assert "/tmp/x" in out


class TestYamlRender:
    def test_list_round_trips(self) -> None:
        f = OutputFormatter(format="yaml", is_tty=False)
        out = f.render(_SAMPLE_LIST)
        assert yaml.safe_load(out) == _SAMPLE_LIST

    def test_dict_round_trips(self) -> None:
        f = OutputFormatter(format="yaml", is_tty=False)
        out = f.render(_SAMPLE_DICT)
        assert yaml.safe_load(out) == _SAMPLE_DICT


class TestTsvRender:
    def test_list_of_dicts_emits_header_plus_rows(self) -> None:
        f = OutputFormatter(format="tsv", is_tty=False)
        out = f.render(_SAMPLE_LIST)
        lines = out.rstrip("\n").split("\n")
        assert lines[0] == "name\ttenant\tcount"
        assert lines[1] == "alice\tacme\t7"
        assert lines[2] == "bob\tacme\t3"

    def test_empty_list_emits_blank_output(self) -> None:
        f = OutputFormatter(format="tsv", is_tty=False)
        assert f.render([]) == ""

    def test_single_dict_treated_as_one_row(self) -> None:
        f = OutputFormatter(format="tsv", is_tty=False)
        out = f.render(_SAMPLE_DICT)
        lines = out.rstrip("\n").split("\n")
        assert lines[0] == "status\titems"
        assert lines[1] == "ok\t42"

    def test_tab_in_value_is_escaped(self) -> None:
        f = OutputFormatter(format="tsv", is_tty=False)
        out = f.render([{"col": "a\tb"}])
        # Tab must not appear inside a field — it would corrupt the row.
        rows = out.rstrip("\n").split("\n")
        assert rows[1].count("\t") == 0  # only header→one row, zero tabs in single col

    def test_missing_key_fills_blank(self) -> None:
        f = OutputFormatter(format="tsv", is_tty=False)
        out = f.render([{"a": 1, "b": 2}, {"a": 3}])
        lines = out.rstrip("\n").split("\n")
        assert lines[0] == "a\tb"
        assert lines[1] == "1\t2"
        assert lines[2] == "3\t"


class TestTableRender:
    def test_list_of_dicts_renders_header_keys(self) -> None:
        f = OutputFormatter(format="table", is_tty=False)
        out = f.render(_SAMPLE_LIST)
        # Rich table output should contain every column header and value.
        for token in ("name", "tenant", "count", "alice", "bob", "acme", "7", "3"):
            assert token in out

    def test_single_dict_renders_key_value(self) -> None:
        f = OutputFormatter(format="table", is_tty=False)
        out = f.render(_SAMPLE_DICT)
        assert "status" in out
        assert "ok" in out
        assert "items" in out
        assert "42" in out

    def test_empty_list_does_not_crash(self) -> None:
        f = OutputFormatter(format="table", is_tty=False)
        out = f.render([])
        # Empty input → empty render or a header-only frame, but no exception.
        assert isinstance(out, str)


class TestEmit:
    def test_emit_writes_to_stream(self) -> None:
        buf = io.StringIO()
        f = OutputFormatter(format="json", is_tty=False, stream=buf)
        f.emit(_SAMPLE_DICT)
        assert json.loads(buf.getvalue()) == _SAMPLE_DICT

    def test_emit_appends_trailing_newline(self) -> None:
        buf = io.StringIO()
        f = OutputFormatter(format="json", is_tty=False, stream=buf)
        f.emit(_SAMPLE_DICT)
        assert buf.getvalue().endswith("\n")
