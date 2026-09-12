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

    @staticmethod
    def _appears_acting(ctx: CheckContext, name: str) -> bool:
        """Distinguish 'Milo died at Epsilon' from 'Milo said'.

        Crude but conservative: a name followed by a verb-ish word, not preceded
        by a death cue in the same sentence.
        """
        import re

        for m in re.finditer(rf"\b{re.escape(name)}\b(?:'s)?\s+([a-z]+)", ctx.draft.text):
            verb = m.group(1)
            sentence_start = ctx.draft.text.rfind(".", 0, m.start()) + 1
            sentence = ctx.draft.text[sentence_start : m.end() + 60].lower()
            if any(cue in sentence for cue in ObjectAndBodyCheck._DEATH_CUES):
                continue
            if verb in {"was", "had", "died", "is", "would", "used", "once", "never"}:
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
