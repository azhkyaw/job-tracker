"""Replay AUTO_MATCH_SCORE / AUTO_MATCH_MARGIN against every stored, scored email.

Read-only. Rebuilds each email's decision from its stored `extraction` using the
real candidate SQL and the real _score(), then sweeps the two thresholds and
reports what each setting would have decided against the recorded outcome.

Ground truth splits in two, and the two answer different questions:

  resolved (106)     a human picked the target BECAUSE the current thresholds
                     sent it to triage. These say what LOOSENING would buy —
                     and, crucially, how often it would land on the wrong
                     application instead.

  auto_matched (190) the matcher already decided. These say what TIGHTENING
                     would cost. They cannot show the current settings making a
                     wrong auto-match (the target is the matcher's own answer),
                     so nothing here is evidence that today's bar is safe.

Fidelity caveats, stated rather than buried:
  * candidates are today's applications filtered to those created on or before
    the email arrived — close to "as of", but title_canonical/company_norm are
    today's values, so a record repaired since replays with its repaired text.
  * `applied_at` inside a candidate row is computed from today's events.
"""
import sys
from pathlib import Path

# Derived, not hardcoded: this repo is checked out at a different absolute path
# on each of the author's two machines.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pipeline import db, matcher, config
from pipeline.email_classifier import Extraction, norm_company

SCORES = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
MARGINS = [0.00, 0.05, 0.10, 0.15, 0.20]


def candidates(conn, user_id, ex, created_before, born):
    company = norm_company(ex.company or "")
    if not company:
        return []
    params = {"user_id": user_id, "company": company, "title": ex.role_title,
              "platform": ex.platform, "cmin": config.COMPANY_TRGM_MIN}
    rows = conn.execute(matcher._CANDIDATES_SQL, params).fetchall()
    if not rows:
        rows = conn.execute(matcher._CANDIDATES_RESCUE_SQL, params).fetchall()
    # Only applications that existed when the email arrived.
    return [r for r in rows if born.get(str(r["application_id"])) is not None
            and born[str(r["application_id"])] <= created_before]


def decide(scored, score_thr, margin_thr):
    if not scored:
        return None
    best_score, best = scored[0]
    margin_ok = len(scored) == 1 or (best_score - scored[1][0]) >= margin_thr
    if best_score >= score_thr and margin_ok:
        return str(best["application_id"])
    return None


def main():
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select id::text, created_at from applications")
        born = {r["id"]: r["created_at"] for r in cur.fetchall()}
        cur.execute("""
            select id::text, user_id, received_at, classification, extraction,
                   matched_application_id::text target, triage_state, match_score
            from emails
            where extraction is not null and matched_application_id is not null
              and triage_state in ('auto_matched','resolved')
            order by received_at""")
        emails = cur.fetchall()

        rows = []
        for e in emails:
            x = e["extraction"]
            ex = Extraction(company=x.get("company"), role_title=x.get("role_title"),
                            platform=x.get("platform") or "other", ats=x.get("ats"),
                            event_date=x.get("event_date"), status_detail=x.get("status_detail"),
                            recruiter=x.get("recruiter"), notes=x.get("notes"))
            occurred_at = matcher._event_time(ex, e["received_at"])
            cands = candidates(conn, e["user_id"], ex, e["received_at"], born)
            pid = ex.platform in ("linkedin", "jobstreet", "indeed")
            scored = sorted(((matcher._score(c, occurred_at, pid), c) for c in cands),
                            key=lambda p: p[0], reverse=True)
            rows.append({"state": e["triage_state"], "target": e["target"],
                         "scored": scored, "cls": e["classification"]})

    print(f"replayed {len(rows)} emails "
          f"({sum(1 for r in rows if r['state']=='resolved')} human-resolved, "
          f"{sum(1 for r in rows if r['state']=='auto_matched')} auto-matched)")
    no_cand = sum(1 for r in rows if not r["scored"])
    print(f"  no candidates at all on replay: {no_cand}\n")

    print(f"{'score':>6} {'margin':>7} | {'RESOLVED (106): would now auto-match':^38} | "
          f"{'AUTO (190): still auto-matches':^32}")
    print(f"{'':>6} {'':>7} | {'right':>7} {'WRONG':>7} {'stays triage':>13} | "
          f"{'same':>6} {'moved':>6} {'to triage':>11}")
    grid = {}
    for s in SCORES:
        for m in MARGINS:
            res_right = res_wrong = res_pend = 0
            auto_same = auto_moved = auto_pend = 0
            for r in rows:
                got = decide(r["scored"], s, m)
                if r["state"] == "resolved":
                    if got is None:
                        res_pend += 1
                    elif got == r["target"]:
                        res_right += 1
                    else:
                        res_wrong += 1
                else:
                    if got is None:
                        auto_pend += 1
                    elif got == r["target"]:
                        auto_same += 1
                    else:
                        auto_moved += 1
            grid[(s, m)] = (res_right, res_wrong, res_pend, auto_same, auto_moved, auto_pend)
            cur_mark = " <- current" if (s, m) == (config.AUTO_MATCH_SCORE,
                                                   config.AUTO_MATCH_MARGIN) else ""
            print(f"{s:>6.2f} {m:>7.2f} | {res_right:>7} {res_wrong:>7} {res_pend:>13} | "
                  f"{auto_same:>6} {auto_moved:>6} {auto_pend:>11}{cur_mark}")
    return grid, rows


if __name__ == "__main__":
    main()
