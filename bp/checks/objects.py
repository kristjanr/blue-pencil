"""Chekhov's inventory, and the rule that dead stays dead.

Both are registry lookups rather than judgments, which is what makes them cheap
enough to run on every candidate. The interesting design point is that
*reversibility of death* lives in the profile: in a series where minds are
backed up, a dead character walking in is not an error — but an unexplained one
is, because acceptance would write it into the graph.
"""

from __future__ import annotations

from ..models import Citation, Marginalium
from . import CheckContext, register


@register("objects")
class ObjectAndBodyCheck:
    """Dead stays dead; a sword cannot be in two places.

    Whether death is reversible is a *profile* question, not a checker question.
    In a series where minds are backed up, a dead character returning is not an
    error — but the restore has to be on the page, or the ledger quietly stops
    matching the story.
    """

    name = "objects"

    _RESTORE = ("restor", "reboot", "reinstant", "backup", "back-up", "revive", "resurrect",
                "woke up in", "new body", "new matrix", "reload")
    _DEATH_CUES = ("dead", "died", "corpse", "killed", "body of", "grave", "funeral", "buried")

    def run(self, ctx: CheckContext) -> list[Marginalium]:
        out: list[Marginalium] = []
        text_low = ctx.draft.text.lower()
        when = ctx.date

        for mention in ctx.mentions("entity"):
            row = ctx.graph.entity(mention.target_id)
            if row is None or row["status"] != "dead":
                continue
            # Only somebody who could be restored can be wrongly un-dead. A
            # faction, place or ship recorded "dead" means disbanded or
            # destroyed, and it reappearing is a different question entirely —
            # on the real corpus this fired as "the Pav is recorded dead but
            # acts in this chapter", the Pav being a species.
            if row["kind"] not in ("character", ""):
                continue
            # A dead character being *spoken about* is fine; being on the page is not.
            if not self._appears_acting(ctx, mention.label):
                continue
            restored = any(cue in text_low for cue in self._RESTORE)
            if ctx.profile.revivable and restored:
                continue
            cites = ctx.graph.citations_for("entity", row["entity_id"])
            msg = (f"{row['name']} is recorded dead in the graph but acts in this chapter")
            fixes = ["cut or rewrite the appearance", "correct the graph if the death record is wrong"]
            if ctx.profile.revivable:
                msg += "; this series allows restoration, but no restore appears on the page"
                fixes.insert(1, "put the restore on the page, and card it as a reveal")
            out.append(Marginalium(
                check=self.name, severity="hard", scene_ref=ctx.draft.ref, line=mention.line,
                excerpt=mention.excerpt, message=msg,
                citations=[Citation(scene=c.scene, quote=c.quote) for c in cites[:2]], fixes=fixes,
            ))

        place = ctx.draft.place or (ctx.card.location if ctx.card else "")
        for mention in ctx.mentions("object"):
            row = ctx.graph.conn.execute(
                "SELECT * FROM objects WHERE object_id=?", (mention.target_id,)
            ).fetchone()
            if row is None or not row["location"] or not place:
                continue
            if row["as_of_day"] is not None and when is not None and row["as_of_day"] > when.hi:
                continue  # the registry entry postdates this chapter
            if row["location"].casefold() == place.casefold():
                continue
            if not self._present_here(ctx, mention.label):
                continue
            out.append(Marginalium(
                check=self.name, severity="hard", scene_ref=ctx.draft.ref, line=mention.line,
                excerpt=mention.excerpt,
                message=(f"{row['name']} is here, but the registry puts it at {row['location']}"
                         + (f" (held by {row['holder']})" if row["holder"] else "")
                         + f" as of {ctx.fmt(row['as_of_day'])}"),
                citations=[Citation(scene=c.scene, quote=c.quote)
                           for c in ctx.graph.citations_for("object", row["object_id"])[:2]],
                fixes=["show how it got here", "correct the registry if it is stale", "use a different object"],
            ))
        return out

    #: Not the character doing something — prepositions, conjunctions and
    #: determiners that follow a name in a list or a phrase. This is the whole
    #: Alan false alarm: "I hadn't thought of Carl, Karen, and Alan in,
    #: literally, centuries" matched `Alan` + `in` and called it an action.
    _NOT_ACTION = frozenset("""
    in on at to for from with and or but of as than the a an by about into over under
    after before while when who whom whose that this these those again too very
    """.split())

    #: The narrator is remembering, not watching.
    _RECALL = ("hadn't thought", "had not thought", "used to", "back when", "i remember",
               "remembered", "in those days", "years ago", "centuries ago", "long before",
               "thought of", "memory of", "reminded")

    #: Irregular pasts that do not end in -ed. A dead character in a past-tense
    #: sentence is being recalled, not resurrected.
    _PAST = frozenset("""
    was were had been did went came saw said told knew took gave made found left ran
    flew built wrote thought became felt kept held lost met sent spoke stood won
    """.split())

    @staticmethod
    def _appears_acting(ctx: CheckContext, name: str) -> bool:
        """Distinguish 'Milo died at Epsilon' from 'Milo said'.

        Only ever asked about a character the graph records as dead, so it is
        deliberately conservative: a missed resurrection is cheap to catch on
        the page, and a false one trains the writer to ignore the check. Three
        ways to not be acting — the sentence carries a death cue, the narrator
        is explicitly remembering, or the verb is in the past tense.
        """
        import re

        text = ctx.draft.text
        for m in re.finditer(rf"\b{re.escape(name)}\b(?:'s)?\s+([a-z]+)", text):
            verb = m.group(1)
            if verb in ObjectAndBodyCheck._NOT_ACTION or verb in ObjectAndBodyCheck._PAST:
                continue
            if verb.endswith("ed"):
                continue
            sentence_start = text.rfind(".", 0, m.start()) + 1
            sentence = text[sentence_start : m.end() + 60].lower()
            if any(cue in sentence for cue in ObjectAndBodyCheck._DEATH_CUES):
                continue
            if any(cue in sentence for cue in ObjectAndBodyCheck._RECALL):
                continue
            return True
        return False

    @staticmethod
    def _present_here(ctx: CheckContext, name: str) -> bool:
        import re

        return bool(re.search(
            rf"\b(?:the\s+)?{re.escape(name)}\b(?:\s+(?:lay|sat|rested|hung|gleamed|was here))|"
            rf"\b(?:drew|held|lifted|carried|touched|unsheathed|raised|took)\s+(?:the\s+|his\s+|her\s+|their\s+)?{re.escape(name)}\b",
            ctx.draft.text, re.I,
        ))
