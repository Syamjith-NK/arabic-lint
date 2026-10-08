"""Find text pre-processed for a renderer that already does the work, in Python source.

`detect.py` finds Arabic that was already corrupted and stored. This finds the code
that will corrupt it at render time, before anything has been stored at all.

Two shapes of the same mistake are reported:

    get_display(reshape(s))   the full recipe: shaping AND reordering applied
    get_display(s)            bidi only: reordering applied, no shaping

The second is the larger population. `python-bidi` is downloaded around 9.4 million
times a month and `arabic-reshaper` far less, so most of the affected code never
touches a reshaper at all. Measured on Pillow 12.2.0 with `raqm: True`, 48px Arial
Unicode, `محمد الفارس` renders as `سرافلا دمحم` through both of them: letters and
words backwards either way. The bidi-only output is the harder one to spot, because
the renderer still joins it correctly and it simply reads in reverse.

The hard part is not finding the pattern. A grep for `get_display(` returns thousands
of files and almost all of that is noise, because **whether the call is wrong depends
entirely on what draws the text**:

    matplotlib >= 3.11   shapes and runs bidi itself  -> pre-processing REVERSES the text
    Pillow with Raqm     shapes and runs bidi itself  -> pre-processing REVERSES the text
    Pillow without Raqm  does neither                 -> pre-processing is REQUIRED
    ReportLab            does neither                 -> pre-processing is REQUIRED
    print() to a tty     terminal-dependent           -> not our call

So a checker that flags every occurrence is worse than useless: it trains people to
ignore it. This one reports only where a shaping renderer is actually imported, and
stays silent everywhere else.

It also stays silent on a helper that is **defined but never called** in a script.
That is not a hypothetical: it is the single most common false positive in the wild.
A library function that *returns* the pre-shaped string is a different shape: the
call that draws it usually lives in the module that imported the helper, so the
return is reported at low confidence when this file itself imports matplotlib or
Pillow. When several files are scanned together, a caller that imports that helper
and passes the result to a drawing call is reported as well. The link is the
import of that function, not "a shaping renderer is imported somewhere": a file
that never calls the helper stays silent. A wordcloud import on its own is not a
drawing call either — wordcloud only draws when a generation method runs.

Standard library only. `ast`, no regex heuristics, no dependencies.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

RESHAPERS = {"reshape", "ArabicReshaper"}
BIDI = {"get_display"}

# Text-drawing calls. Importing matplotlib or PIL proves nothing on its own: a file
# can import Pillow to *load* an image and then print() the Arabic to a terminal,
# which is not our business. Something must actually draw.
MPL_CALLS = {
    "title", "suptitle", "xlabel", "ylabel", "set_title", "set_xlabel", "set_ylabel",
    "set_xticklabels", "set_yticklabels", "annotate", "legend", "bar_label",
}
PIL_CALLS = {"multiline_text", "Draw"}
# wordcloud draws each word with PIL.ImageDraw.text, so it inherits Raqm from
# whatever Pillow is installed. These are NOT in DRAWING_CALLS: `generate` is also
# what a transformers model calls, and a bare attribute match would blame wordcloud
# for a file that never imported it. They count only after the import check below,
# and `generate` only when the receiver is a WordCloud.
WORDCLOUD_SPECIFIC = {"generate_from_frequencies", "generate_from_text"}
AMBIGUOUS_CALLS = {"text"}                       # both libraries spell it `text`
DRAWING_CALLS = MPL_CALLS | PIL_CALLS | AMBIGUOUS_CALLS

# Modules whose text APIs shape and reorder complex scripts themselves. Handing
# them a pre-shaped string means the work is done twice.
SHAPING_SINKS = {
    "matplotlib": "matplotlib >= 3.11 shapes text with libraqm and applies bidi itself",
    "PIL": "Pillow built with Raqm shapes text and applies bidi itself",
    "wordcloud": "wordcloud draws through Pillow, which shapes text when built with Raqm",
}

# A returned string can be drawn by another module. matplotlib and Pillow are the
# sinks whose drawing call can live over there. wordcloud is not in this set: its
# drawing calls are the generate methods, and an import with nothing generated has
# to stay silent (a colormap import of matplotlib beside an unused WordCloud is the
# usual shape of that file).
HANDOFF_SINKS = ("matplotlib", "PIL")

# Modules that do no shaping and no bidi. The recipe is correct for these, and
# reporting them is how a linter loses its users.
PASSIVE_SINKS = {"reportlab", "fpdf", "fpdf2"}

# What was done to the string before it reached the renderer. Both are bugs on a
# shaping sink, they are not the same bug, and the message has to say which.
RECIPE = "recipe"        # get_display(reshape(s)) - shaped AND reordered
BIDI_ONLY = "bidi-only"  # get_display(s)          - reordered, never shaped
HEADLINE = {
    RECIPE: "pre-shaped text passed to",
    BIDI_ONLY: "pre-reordered text passed to",
}
# Appended to the sink's description to make one sentence. The bidi-only wording is
# not the recipe's wording with a word changed: what reaches the renderer is a
# different string. Rendered on Pillow 12.2.0 with `raqm: True` at 48px, both turn
# `محمد الفارس` into `سرافلا دمحم`, but only the full recipe leaves presentation
# forms behind. Bidi alone hands over ordinary letters in the wrong order, so the
# renderer joins them perfectly and the result is clean, fluent, backwards Arabic.
REASON_TAIL = {
    RECIPE: ", so this reorders an already-reordered string and the text renders reversed",
    BIDI_ONLY: ", so this reverses a string it will reverse again and the words render "
               "back to front. No presentation forms are produced, so the output stays "
               "correctly joined and is only out of order, which is harder to spot",
}


@dataclass
class SourceFinding:
    line: int
    col: int
    snippet: str
    sink: str          # "matplotlib" | "PIL" | "wordcloud" | "unknown"
    reason: str
    confidence: str    # "high" | "conditional" | "low"
    fix: str | None = None        # replacement source for this expression, if safe
    unfixable_why: str | None = None
    end_line: int = 0             # full span of the call, for applying the rewrite
    end_col: int = 0
    kind: str = RECIPE            # RECIPE | BIDI_ONLY

    def format(self, path: str) -> str:
        head = (f"{path}:{self.line}:{self.col}: "
                f"{HEADLINE[self.kind]} {self.sink}")
        if self.confidence == "conditional":
            head += "  [CONDITIONAL]"
        elif self.confidence == "low":
            head += "  [LOW]"
        return f"{head}\n    {self.snippet}\n    {self.reason}"


@dataclass
class SourceReport:
    findings: list[SourceFinding] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # why we stayed quiet

    @property
    def ok(self) -> bool:
        return not self.findings


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: set[str] = set()
        # Local names bound to the reshaper / bidi entry points. Real code aliases
        # them (`from bidi.algorithm import get_display as _bidi_get_display`), and a
        # checker that only knows the canonical spelling silently passes the file.
        self.bidi_names: set[str] = set(BIDI)
        # Names this file actually imported from `bidi`, which is a much stricter
        # set than `bidi_names`. It has to be, because `get_display` on its own is
        # a generic name: code search returns hundreds of unrelated projects that
        # define their own `def get_display(self)` on a display, a dashboard or a
        # blackjack hand. Inside `get_display(reshape(x))` the reshaper proves what
        # the call is; alone it proves nothing, so a bidi-only finding is raised
        # only against a name that demonstrably came from python-bidi.
        self.bidi_imported_names: set[str] = set()
        self.reshape_names: set[str] = set(RESHAPERS)
        self.referenced: set[str] = set()
        self.drawing: set[str] = set()
        # Variables holding the output of reshape(). The two halves of the recipe are
        # very often on separate lines:
        #     reshaped  = arabic_reshaper.reshape(segment)
        #     processed = get_display(reshaped)
        # Looking only inside the get_display() call misses every one of those.
        self.reshaped_vars: set[str] = set()
        self.preshape_calls: list[ast.Call] = []
        self.bidi_only_calls: list[ast.Call] = []
        # name -> node, for helpers like `def arab(t): return get_display(reshape(t))`
        self.preshape_funcs: dict[str, ast.FunctionDef] = {}
        self.called_names: set[str] = set()
        # Names that denote WordCloud or an instance built from it in this file.
        # `generate` is only a drawing call on one of these; the two generate_from_*
        # names are specific enough that the import alone qualifies them.
        self.wordcloud_names: set[str] = set()
        self.wordcloud_drawing: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            root = a.name.split(".")[0]
            self.imports.add(root)
            if a.asname and root in {"arabic_reshaper"}:
                self.reshape_names.add(a.asname)
            if root == "bidi":
                # `import bidi.algorithm` then `bidi.algorithm.get_display(x)`, or
                # `import bidi.algorithm as ba` then `ba.get_display(x)`. Either way
                # the attribute this file calls is spelled `get_display`.
                self.bidi_imported_names |= BIDI
            if root == "wordcloud":
                # `import wordcloud` then `wordcloud.WordCloud()`. The attribute is
                # still spelled WordCloud; an alias renames the module, not the class.
                self.wordcloud_names.add("WordCloud")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root = node.module.split(".")[0]
            self.imports.add(root)
            for a in node.names:
                if a.name in BIDI and a.asname:
                    self.bidi_names.add(a.asname)
                if a.name in RESHAPERS and a.asname:
                    self.reshape_names.add(a.asname)
                if root == "bidi" and a.name in BIDI:
                    self.bidi_imported_names.add(a.asname or a.name)
                if root == "wordcloud" and a.name in {"WordCloud", "*"}:
                    self.wordcloud_names.add(a.asname or "WordCloud")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.referenced.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.referenced.add(node.attr)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if _contains_preshape(node):
            self.preshape_funcs[node.name] = node
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if _contains_reshape(node.value, self.reshape_names):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self.reshaped_vars.add(t.id)
        if _constructs_wordcloud(node.value, self.wordcloud_names):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self.wordcloud_names.add(t.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (node.value is not None and isinstance(node.target, ast.Name)
                and _constructs_wordcloud(node.value, self.wordcloud_names)):
            self.wordcloud_names.add(node.target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee(node)
        if name:
            self.called_names.add(name)
            if name in DRAWING_CALLS:
                self.drawing.add(name)
            elif _is_wordcloud_draw(node, name, self.wordcloud_names):
                # Held aside until the walk finishes. The import that makes this a
                # wordcloud call may sit below it, and `generate` must not become a
                # drawing call in a file that never imports wordcloud.
                self.wordcloud_drawing.add(name)
        if name in self.bidi_names and (
            _contains_reshape(node, self.reshape_names)
            or any(isinstance(a, ast.Name) and a.id in self.reshaped_vars for a in node.args)
        ):
            self.preshape_calls.append(node)
        elif name in self.bidi_imported_names:
            # Reordering with no reshaping anywhere near it. Same bug on a shaping
            # renderer, different mechanism, so it is tracked separately and reported
            # in its own words. Everything that decides whether it is a bug at all
            # (which renderer, does anything draw, is the helper dead) is shared.
            self.bidi_only_calls.append(node)
        self.generic_visit(node)


def _callee(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _contains_reshape(node: ast.AST, names: set[str] | None = None) -> bool:
    names = names or RESHAPERS
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and _callee(sub) in names:
            return True
    return False


def _contains_preshape(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and _callee(sub) in BIDI and _contains_reshape(sub):
            return True
    return False


def _snippet(lines: list[str], lineno: int) -> str:
    try:
        return lines[lineno - 1].strip()[:110]
    except IndexError:
        return ""


def scan_source(text: str, linked: dict[str, str] | None = None) -> SourceReport:
    """Scan one Python source file. Never raises on unparseable input.

    `linked` maps a name this file imported (`rtl_safe`, or `helper.rtl_safe`) to
    the kind of recipe that function returns. It is how a drawing call in this
    file can be reported when the recipe itself lives in the helper. Names only
    arrive here when the import resolves to a scanned file that really does
    return a pre-shaped string; a matplotlib import elsewhere is not a link.
    """
    report = SourceReport()
    linked = linked or {}
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        report.skipped.append(f"could not parse: {exc.msg}")
        return report

    v = _Visitor()
    v.visit(tree)
    # `generate` is a drawing call only for a file that imports wordcloud, and only
    # when the receiver was a WordCloud (see _is_wordcloud_draw). Doing this after
    # the walk means a late import still counts.
    if "wordcloud" in v.imports:
        v.drawing |= v.wordcloud_drawing
    else:
        v.wordcloud_drawing.clear()
    # (call, kind) in source order. The two kinds differ only in what the message
    # says: every gate below is shared, because what makes either of them a bug is
    # the same question about the renderer.
    candidates = ([(c, RECIPE) for c in v.preshape_calls]
                  + [(c, BIDI_ONLY) for c in v.bidi_only_calls])
    # Drawing calls in THIS file whose argument is an imported helper that returns
    # a pre-shaped string. The helper's own file may import nothing that draws.
    linked_sites = _linked_draw_sites(tree, v, linked) if linked else []
    if not candidates and not linked_sites:
        return report
    candidates.sort(key=lambda ck: (ck[0].lineno, ck[0].col_offset))

    # What to call it in the skipped messages, so a bidi-only file is not told that
    # "pre-shaping" was found when no reshaper was involved.
    found = "pre-shaping" if v.preshape_calls else "bidi reordering"

    lines = text.splitlines()

    # Which renderer is in play? A file can import both; report the shaping ones.
    shaping = sorted(v.imports & set(SHAPING_SINKS))
    passive = sorted(v.imports & PASSIVE_SINKS)

    if not shaping:
        # A linked helper is only a bug here if THIS file draws with a shaping
        # renderer. ReportLab calling the same helper is the recipe used correctly.
        if candidates:
            if passive:
                report.skipped.append(
                    f"{found} found, but the only renderer imported is {', '.join(passive)}, "
                    "which does no shaping of its own. The recipe is correct here."
                )
            else:
                report.skipped.append(
                    f"{found} found, but no shaping renderer is imported in this file. "
                    "Nothing to say without knowing what draws the text."
                )
        return report

    # Order matters: classify the renderer first, then ask whether anything draws.
    # Checking "does it draw" first made the ReportLab branch unreachable and reported
    # a correct file with a misleading reason.
    if not v.drawing:
        # The drawing call is often in the module that imports this one. Report the
        # return itself, at low confidence, rather than requiring a second file —
        # but only for a library, and only for matplotlib or Pillow imported *here*.
        # A script that prints the result is doing something we can see, and an
        # uncalled helper next to a real `plt.title(...)` is dead code, handled
        # below. wordcloud is not a handoff sink: nothing generated stays silent.
        handed = _explicitly_returned(tree, [c for c, _ in candidates])
        sinks = [name for name in HANDOFF_SINKS if name in shaping]
        if handed and sinks and not _runs_at_import(tree):
            sink = sinks[0]
            handed_ids = {id(c) for c in handed}
            for call, kind in candidates:
                if id(call) not in handed_ids:
                    continue
                _append_returned(report, call, kind, sink, v, text, lines)
            return report
        report.skipped.append(
            f"{found} found and a shaping renderer is imported, but nothing in this "
            "file draws text with it. Importing Pillow to load an image and then "
            "print()ing the Arabic is not this tool's business."
        )
        return report

    # A helper that is defined and never called cannot corrupt anything. This is the
    # commonest false positive in real repositories: the import and the helper are
    # left behind after the code that used them was deleted.
    live_calls = []
    for call, kind in candidates:
        enclosing = _enclosing_func(tree, call)
        # Conservative on purpose. A helper invoked as `self.f()`, passed by name, or
        # called from another module is NOT dead, and a checker that guesses wrong here
        # goes quiet on a real bug. Only a name that appears nowhere outside its own
        # def counts as dead.
        if enclosing and enclosing not in v.referenced and _is_script(tree):
            did = "pre-shapes" if kind == RECIPE else "reorders text"
            report.skipped.append(
                f"{enclosing}() {did} but is never called in this file (dead code)"
            )
            continue
        live_calls.append((call, kind))

    # Which renderer, when a file imports both? Decide on the drawing calls seen, not
    # on alphabetical order, or a matplotlib bug gets reported against Pillow.
    # wordcloud has to win over a matplotlib import that is only there for a colormap:
    # `matplotlib.colormaps` is not a drawing call, and blaming matplotlib for a
    # WordCloud().generate_from_frequencies(...) names the wrong renderer.
    if len(shaping) == 1:
        sink = shaping[0]
    elif v.wordcloud_drawing and not (v.drawing & MPL_CALLS):
        sink = "wordcloud"
    elif v.drawing & MPL_CALLS:
        sink = "matplotlib"
    elif v.drawing & PIL_CALLS:
        sink = "PIL"
    else:
        sink = shaping[0]

    for call, kind in live_calls:
        fix, why = _plan_fix(call, v, text, kind)
        report.findings.append(
            SourceFinding(
                line=call.lineno,
                col=call.col_offset + 1,
                snippet=_snippet(lines, call.lineno),
                sink=sink,
                reason=SHAPING_SINKS[sink] + REASON_TAIL[kind],
                # Neither condition is knowable from source alone: the installed
                # matplotlib version, and whether Pillow was built with Raqm.
                confidence="conditional",
                fix=fix,
                unfixable_why=why,
                end_line=getattr(call, "end_lineno", call.lineno),
                end_col=getattr(call, "end_col_offset", 0) + 1,
                kind=kind,
            )
        )
    for call, kind in linked_sites:
        # The recipe is in another file. Deleting this call would drop the text on
        # the floor; the repair belongs with the helper, and only if every caller
        # draws with a shaping renderer.
        report.findings.append(
            SourceFinding(
                line=call.lineno,
                col=call.col_offset + 1,
                snippet=_snippet(lines, call.lineno),
                sink=sink,
                reason=SHAPING_SINKS[sink] + REASON_TAIL[kind],
                confidence="conditional",
                fix=None,
                unfixable_why=("the recipe is in the helper this calls. Removing the "
                               "call here drops the text; change the helper, and only "
                               "if every caller draws with a shaping renderer."),
                end_line=getattr(call, "end_lineno", call.lineno),
                end_col=getattr(call, "end_col_offset", 0) + 1,
                kind=kind,
            )
        )
    return report


def _append_returned(report: SourceReport, call: ast.Call, kind: str, sink: str,
                     v: "_Visitor", text: str, lines: list[str]) -> None:
    """A pre-shaped value handed to another module. Reported, never rewritten.

    `--fix` deletes the recipe. That is right when this file draws with matplotlib
    or Pillow, and wrong when the caller draws with ReportLab or with Pillow that
    was built without Raqm. The caller is not in this file, so the rewrite is not
    ours to make.
    """
    _, why = _plan_fix(call, v, text, kind)
    if why is None:
        why = ("this value is returned to a caller, so whether removing the recipe "
               "is safe depends on what that caller draws. --fix will not rewrite it.")
    else:
        why += " This value is also returned to a caller, so --fix will not rewrite it."
    report.findings.append(
        SourceFinding(
            line=call.lineno,
            col=call.col_offset + 1,
            snippet=_snippet(lines, call.lineno),
            sink=sink,
            reason=(SHAPING_SINKS[sink] + REASON_TAIL[kind]
                    + "; this value is returned to a caller"),
            confidence="low",
            fix=None,
            unfixable_why=why,
            end_line=getattr(call, "end_lineno", call.lineno),
            end_col=getattr(call, "end_col_offset", 0) + 1,
            kind=kind,
        )
    )


def _is_wordcloud_draw(node: ast.Call, name: str, names: set[str]) -> bool:
    """True for a wordcloud generation call, ignoring a bare `model.generate()`."""
    if name in WORDCLOUD_SPECIFIC:
        return True
    if name != "generate":
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    return _is_wordcloud_receiver(func.value, names)


def _is_wordcloud_receiver(node: ast.AST, names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in names or node.id == "WordCloud"
    if isinstance(node, ast.Call):
        return _constructs_wordcloud(node, names)
    # wordcloud.WordCloud.generate(...) — the class itself, not an instance.
    return isinstance(node, ast.Attribute) and node.attr == "WordCloud"


def _constructs_wordcloud(node: ast.AST, names: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in names or func.id == "WordCloud"
    return isinstance(func, ast.Attribute) and func.attr == "WordCloud"


def _explicitly_returned(tree: ast.AST, calls: list[ast.Call]) -> list[ast.Call]:
    """Calls whose value is what a function returns, not merely computed on the way.

    `return get_display(reshape(text))` and `shaped = get_display(...); return shaped`
    both count. `return len(get_display(...))` does not: the caller never receives
    the string. `item[1] = get_display(...); return result` does not either, which
    is the OCR shape that reorders a list and must stay silent.
    """
    wanted = {id(c) for c in calls}
    found: set[int] = set()

    class Finder(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._scan(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)

        def _scan(self, func: ast.AST) -> None:
            flowing = _names_flowing_to_return(func)
            for sub in _walk_local(func):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    for leaf in _returned_leaves(sub.value):
                        if isinstance(leaf, ast.Call) and id(leaf) in wanted:
                            found.add(id(leaf))
                if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                    value = sub.value
                    target_names = _assign_names(sub)
                    if (isinstance(value, ast.Call) and id(value) in wanted
                            and target_names & flowing):
                        found.add(id(value))

    Finder().visit(tree)
    return [c for c in calls if id(c) in found]


def _assign_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.AnnAssign):
        return {node.target.id} if isinstance(node.target, ast.Name) else set()
    names = set()
    for target in node.targets:
        if isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _names_flowing_to_return(func: ast.AST) -> set[str]:
    """Names whose value is returned, following simple `out = shaped` aliases."""
    returned: set[str] = set()
    aliases: list[tuple[str, str]] = []
    for sub in _walk_local(func):
        if isinstance(sub, ast.Return) and sub.value is not None:
            for leaf in _returned_leaves(sub.value):
                if isinstance(leaf, ast.Name):
                    returned.add(leaf.id)
        elif (isinstance(sub, ast.Assign) and len(sub.targets) == 1
              and isinstance(sub.targets[0], ast.Name)
              and isinstance(sub.value, ast.Name)):
            aliases.append((sub.targets[0].id, sub.value.id))
    changed = True
    while changed:
        changed = False
        for target, source in aliases:
            if target in returned and source not in returned:
                returned.add(source)
                changed = True
    return returned


def _returned_leaves(node: ast.AST):
    """Expressions a return actually hands back, without entering other calls.

    Stopping at a Call is what keeps `return len(get_display(s))` quiet: the
    caller receives an int. Tuple elements, dict values and `x if cond else y`
    are handed back, so those leaves are the returned strings.
    """
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            yield from _returned_leaves(elt)
        return
    if isinstance(node, ast.Dict):
        for key in node.keys:
            if key is not None:
                yield from _returned_leaves(key)
        for value in node.values:
            yield from _returned_leaves(value)
        return
    if isinstance(node, ast.IfExp):
        yield from _returned_leaves(node.body)
        yield from _returned_leaves(node.orelse)
        return
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from _returned_leaves(value)
        return
    yield node


def _walk_local(node: ast.AST):
    """Yield `node` and its descendants, not the bodies of nested defs or classes."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield from _walk_local(child)


def _runs_at_import(tree: ast.AST) -> bool:
    """Does this module do work when it is imported, rather than only define names?

    A bare `plt.title(...)` or `print(...)` at module level is a script: the
    pre-shaped string is consumed here, and an uncalled helper beside it is dead.
    `if __name__ == "__main__"` is the opposite — it is how a library stays
    importable — so a helper above that guard is still returned to a caller.
    """
    for node in getattr(tree, "body", []):
        if _is_main_guard(node):
            continue
        if not isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef, ast.Assign,
                                 ast.AnnAssign, ast.Expr)):
            return True
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return True
    return False


def _is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    if not isinstance(test.ops[0], (ast.Eq, ast.NotEq)):
        return False
    left, right = test.left, test.comparators[0]
    return ((_is_dunder_name(left) and _is_main_string(right))
            or (_is_dunder_name(right) and _is_main_string(left)))


def _is_dunder_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "__name__"


def _is_main_string(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "__main__"


def returned_helpers(text: str) -> dict[str, str]:
    """Module-level functions that return a pre-shaped string: name -> kind.

    This does not decide that the return is a bug. It lets another file see that
    `from helper_module import rtl_safe` is the recipe, which is not knowable from
    the caller alone. A shaping import is deliberately not required here: the
    caller is where that question gets asked.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    v = _Visitor()
    v.visit(tree)
    pairs = ([(c, RECIPE) for c in v.preshape_calls]
             + [(c, BIDI_ONLY) for c in v.bidi_only_calls])
    if not pairs:
        return {}
    handed = {id(c) for c in _explicitly_returned(tree, [c for c, _ in pairs])}
    module_level = {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    out: dict[str, str] = {}
    for call, kind in pairs:
        if id(call) not in handed:
            continue
        name = _innermost_func(tree, call)
        if name not in module_level:
            continue
        # The full recipe is the one to name if a function returns both shapes.
        if name not in out or kind == RECIPE:
            out[name] = kind
    return out


def helpers_imported(text: str, importer: Path, exports: dict[Path, dict[str, str]]) -> dict[str, str]:
    """Local names bound to a helper that returns a pre-shaped string.

    Resolution follows the import's dotted path, starting at the importer's
    directory and walking up a few parents, and stops at the first file that
    exists. A link is made only when that file was scanned and actually returns
    a pre-shaped string. `matplotlib` imported by a file that does not call the
    helper never becomes an entry. Keys are `rtl_safe` or `helper.rtl_safe`.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    linked: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = _resolve_import(importer, node.module, node.level or 0)
            funcs = exports.get(target) if target is not None else None
            if not funcs:
                continue
            for alias in node.names:
                if alias.name == "*":
                    for fname, kind in funcs.items():
                        _remember_helper(linked, fname, kind)
                elif alias.name in funcs:
                    _remember_helper(linked, alias.asname or alias.name, funcs[alias.name])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # `import pkg.mod` binds the name `pkg`, and the call is then
                # `pkg.mod.rtl_safe`. Only a module imported under its own last
                # component (`import helper_module`) is a local name we can see.
                if "." in alias.name and not alias.asname:
                    continue
                target = _resolve_import(importer, alias.name, 0)
                funcs = exports.get(target) if target is not None else None
                if not funcs:
                    continue
                mod_name = alias.asname or alias.name
                for fname, kind in funcs.items():
                    _remember_helper(linked, f"{mod_name}.{fname}", kind)
    return linked


def _remember_helper(linked: dict[str, str], name: str, kind: str) -> None:
    if name not in linked or kind == RECIPE:
        linked[name] = kind


def _resolve_import(importer: Path, module: str | None, level: int) -> Path | None:
    """The scanned file an import most likely names, or None."""
    parts = [p for p in (module or "").split(".") if p]
    try:
        importer = importer.resolve()
    except OSError:
        importer = Path(importer)
    if level:
        base = importer.parent
        for _ in range(level - 1):
            base = base.parent
        return _as_python_file(base.joinpath(*parts) if parts else base)
    base = importer.parent
    for _ in range(6):
        found = _as_python_file(base.joinpath(*parts) if parts else base)
        if found is not None:
            return found
        if base.parent == base:
            break
        base = base.parent
    return None


def _as_python_file(candidate: Path) -> Path | None:
    py = candidate if candidate.suffix == ".py" else Path(str(candidate) + ".py")
    if py.is_file():
        return py.resolve()
    init = candidate / "__init__.py"
    if init.is_file():
        return init.resolve()
    return None


def _linked_draw_sites(tree: ast.AST, v: "_Visitor",
                       linked: dict[str, str]) -> list[tuple[ast.Call, str]]:
    """Calls to an imported helper that are arguments of a drawing call."""
    sites: list[tuple[ast.Call, str]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _call_draws(node, v):
            continue
        for sub in ast.walk(node):
            if sub is node or not isinstance(sub, ast.Call):
                continue
            key = _imported_call_key(sub)
            kind = linked.get(key) if key else None
            if kind and id(sub) not in seen:
                seen.add(id(sub))
                sites.append((sub, kind))
    sites.sort(key=lambda ck: (ck[0].lineno, ck[0].col_offset))
    return sites


def _call_draws(node: ast.Call, v: "_Visitor") -> bool:
    name = _callee(node)
    if not name:
        return False
    if name in DRAWING_CALLS and name in v.drawing:
        return True
    return "wordcloud" in v.imports and _is_wordcloud_draw(node, name, v.wordcloud_names)


def _imported_call_key(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _plan_fix(call: ast.Call, v: "_Visitor", text: str,
              kind: str = RECIPE) -> tuple[str | None, str | None]:
    """Can this one call be rewritten mechanically, and if not, why not?

    Safe:   get_display(reshape(EXPR))   ->   EXPR
            Both halves of the recipe are inside this expression, so removing the
            expression removes the whole recipe. Nothing else refers to the result.

    NOT YET:  get_display(EXPR)   ->   EXPR
            The bidi-only form. It looks like the same rewrite and it probably is,
            but `--fix` has not been validated against it, and a rewriter that
            edits somebody's source on a "probably" is not one to ship. Reported,
            and left strictly alone, until that is done as its own piece of work.

    NOT safe:  reshaped = reshape(x)  /  out = get_display(reshaped)
            Rewriting the second line to `out = reshaped` deletes the bidi half and
            leaves the shaping half applied. A shaping renderer still reorders it,
            but ligatures can differ; Pillow without Raqm needs the deleted bidi step.
            The two lines must be handled together, and which other code reads
            `reshaped` is not knowable from this expression.
    """
    if kind == BIDI_ONLY:
        return None, ("this is the bidi-only form, and --fix has not been validated "
                      "against it yet. Removing the call is very likely right where the "
                      "renderer shapes, but check it by hand rather than in bulk.")
    if len(call.args) != 1:
        return None, "the call does not take exactly one argument"
    arg = call.args[0]

    # split-statement form: the argument is a variable assigned from reshape()
    if isinstance(arg, ast.Name) and arg.id in v.reshaped_vars:
        return None, (f"reshape() is applied on an earlier line to `{arg.id}`. Removing only "
                      "this call leaves the shaping applied: this can cause ligature "
                      "differences on a shaping renderer, and wrong text on Pillow without "
                      "Raqm because it needs the reordering. Both lines must be handled "
                      f"together, and which other code reads `{arg.id}` is not knowable "
                      "from this expression. Pass the original string to a shaping renderer, "
                      "or gate both steps on the renderer's shaping support.")

    inner = arg if isinstance(arg, ast.Call) and _callee(arg) in v.reshape_names else None
    if inner is None:
        return None, "the argument is not a direct reshape() call"
    if len(inner.args) != 1:
        return None, "reshape() does not take exactly one argument"

    try:
        replacement = ast.get_source_segment(text, inner.args[0])
    except Exception:
        replacement = None
    if not replacement:
        return None, "could not recover the original expression text"
    return replacement, None


def _is_script(tree: ast.AST) -> bool:
    """Does this module DO anything at import time, or is it only definitions?

    An unreferenced helper in a script is dead: nothing else imports a script. The
    same helper in a library module is almost certainly called from another file, and
    treating it as dead there is a false negative on real library code, which is the
    more expensive mistake.
    """
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef, ast.Assign,
                                 ast.AnnAssign, ast.Expr)):
            return True
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return True   # a bare top-level call is a script doing work
    return False


def _enclosing_func(tree: ast.AST, target: ast.AST) -> str | None:
    """Name of the function a node sits in, or None at module level."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if sub is target:
                    return node.name
    return None


def _innermost_func(tree: ast.AST, target: ast.AST) -> str | None:
    """Name of the tightest function containing `target`, or None at module level."""
    found = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if sub is target:
                    found = node.name
                    break
    return found


def apply_fixes(text: str, findings: list[SourceFinding]) -> tuple[str, int]:
    """Rewrite the fixable findings in one file. Returns (new_text, count).

    Applied last-first so that an earlier rewrite never shifts the offsets of one
    still to come. Findings without a fix are left strictly alone.
    """
    lines = text.splitlines(keepends=True)
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln)

    def offset(line: int, col: int) -> int:
        return starts[line - 1] + (col - 1)

    todo = [f for f in findings if f.fix]
    todo.sort(key=lambda f: (f.line, f.col), reverse=True)
    out = text
    for f in todo:
        a, b = offset(f.line, f.col), offset(f.end_line, f.end_col)
        out = out[:a] + f.fix + out[b:]
    return out, len(todo)
