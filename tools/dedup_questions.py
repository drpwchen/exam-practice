"""Merge confirmed duplicate questions and remove redundant rows.

Duplicate = identical stem AND identical options AND identical correct_letter
(plus two OCR-variance pairs verified by hand). Stem-only matches are NOT
treated as duplicates — several share a boilerplate stem but are different
questions, so they are excluded here.

Safety invariants (asserted before commit; rollback on any failure):
  - user_answers: rows are only RE-POINTED, never deleted. Total count
    unchanged; each keeper gains exactly the loser's answer count.
  - users / questions counts: users unchanged; questions drops by len(LOSERS).
  - user_question_state: merged per user (one row per user+question), keeping
    the most-recently-reviewed / higher-reps row. This is scheduling state,
    not answer history.

Usage:
  python dedup_questions.py --db ./data/app.db --dry
  python dedup_questions.py --db ./data/app.db --apply
"""
import argparse
import sqlite3
import sys

# loser_id -> keeper_id  (keeper text is the one shown after merge)
MERGE = {
    1923: 371, 1928: 376, 1929: 377, 1843: 379, 1846: 382,
    1847: 383, 1848: 384, 1851: 387, 1852: 388,
    1845: 381, 1849: 385, 1850: 386,
    1924: 372,          # OCR-variance, visible-identical, keep lower id
    374: 1926,          # OCR-variance, 1926 cleaner ("一" vs "㇐"), keep 1926
}

REPOINT_TABLES = [
    "user_answers", "question_revisions", "question_correction_log",
    "error_reports", "question_paraphrases",
]


def log(msg):
    print(msg)


def merge_state(conn, loser, keeper, dry):
    """Per-user merge of user_question_state from loser into keeper."""
    rows = conn.execute(
        "SELECT user_id FROM user_question_state WHERE question_id=? ",
        (loser,),
    ).fetchall()
    for (uid,) in rows:
        # is_starred is a union: a star under EITHER id must survive the merge.
        starred = conn.execute(
            "SELECT MAX(is_starred) FROM user_question_state "
            "WHERE user_id=? AND question_id IN (?,?)", (uid, loser, keeper),
        ).fetchone()[0]
        k = conn.execute(
            "SELECT reps, last_review_at FROM user_question_state "
            "WHERE user_id=? AND question_id=?", (uid, keeper),
        ).fetchone()
        if k is None:
            log(f"    state u{uid}: repoint {loser}->{keeper}")
            if not dry:
                conn.execute(
                    "UPDATE user_question_state SET question_id=? "
                    "WHERE user_id=? AND question_id=?", (keeper, uid, loser),
                )
        else:
            l = conn.execute(
                "SELECT reps, last_review_at FROM user_question_state "
                "WHERE user_id=? AND question_id=?", (uid, loser),
            ).fetchone()
            # keep the row with the later last_review_at; tiebreak higher reps
            keep_loser = (l[1] or "") > (k[1] or "") or (
                (l[1] or "") == (k[1] or "") and (l[0] or 0) > (k[0] or 0)
            )
            log(f"    state u{uid}: both exist -> keep "
                f"{'loser' if keep_loser else 'keeper'} (reps {l[0]} vs {k[0]})")
            if not dry:
                if keep_loser:
                    conn.execute(
                        "DELETE FROM user_question_state "
                        "WHERE user_id=? AND question_id=?", (uid, keeper))
                    conn.execute(
                        "UPDATE user_question_state SET question_id=? "
                        "WHERE user_id=? AND question_id=?", (keeper, uid, loser))
                else:
                    conn.execute(
                        "DELETE FROM user_question_state "
                        "WHERE user_id=? AND question_id=?", (uid, loser))
        if not dry and starred:
            conn.execute(
                "UPDATE user_question_state SET is_starred=1 "
                "WHERE user_id=? AND question_id=?", (uid, keeper))


def merge_notes(conn, loser, keeper, dry):
    """Merge user_notes (UNIQUE user_id,question_id). On collision, append the
    loser's body to the keeper's so no hand-written note is lost."""
    rows = conn.execute(
        "SELECT user_id, body, updated_at FROM user_notes WHERE question_id=?",
        (loser,),
    ).fetchall()
    for uid, body, upd in rows:
        k = conn.execute(
            "SELECT body FROM user_notes WHERE user_id=? AND question_id=?",
            (uid, keeper),
        ).fetchone()
        if k is None:
            log(f"    note u{uid}: repoint {loser}->{keeper}")
            if not dry:
                conn.execute(
                    "UPDATE user_notes SET question_id=? "
                    "WHERE user_id=? AND question_id=?", (keeper, uid, loser))
        else:
            log(f"    note u{uid}: MERGE (append loser into keeper)")
            if not dry:
                merged = (k[0] or "").rstrip() + "\n---\n" + (body or "")
                conn.execute(
                    "UPDATE user_notes SET body=?, updated_at=? "
                    "WHERE user_id=? AND question_id=?",
                    (merged, upd, uid, keeper))
                conn.execute(
                    "DELETE FROM user_notes WHERE user_id=? AND question_id=?",
                    (uid, loser))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.apply == args.dry:
        sys.exit("pick exactly one of --dry / --apply")
    dry = args.dry

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")

    losers = list(MERGE)
    before_ans = conn.execute("SELECT COUNT(*) FROM user_answers").fetchone()[0]
    before_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    before_q = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    # expected per-keeper answer gain
    gain = {}
    for loser, keeper in MERGE.items():
        n = conn.execute(
            "SELECT COUNT(*) FROM user_answers WHERE question_id=?", (loser,)
        ).fetchone()[0]
        gain[keeper] = gain.get(keeper, 0) + n
    keeper_before = {
        k: conn.execute(
            "SELECT COUNT(*) FROM user_answers WHERE question_id=?", (k,)
        ).fetchone()[0] for k in set(MERGE.values())
    }

    conn.execute("BEGIN")
    for loser, keeper in MERGE.items():
        log(f"  group: delete {loser}, merge into {keeper}")
        merge_state(conn, loser, keeper, dry)
        merge_notes(conn, loser, keeper, dry)
        # tags: copy missing then drop loser's
        if not dry:
            conn.execute(
                "INSERT OR IGNORE INTO question_tags(question_id, tag_id) "
                "SELECT ?, tag_id FROM question_tags WHERE question_id=?",
                (keeper, loser))
            conn.execute("DELETE FROM question_tags WHERE question_id=?", (loser,))
        # images: if keeper already has images, drop loser's; else re-point
        kimg = conn.execute(
            "SELECT COUNT(*) FROM question_images WHERE question_id=?", (keeper,)
        ).fetchone()[0]
        if not dry:
            if kimg > 0:
                conn.execute("DELETE FROM question_images WHERE question_id=?", (loser,))
            else:
                conn.execute(
                    "UPDATE question_images SET question_id=? WHERE question_id=?",
                    (keeper, loser))
        # straight re-point tables
        for t in REPOINT_TABLES:
            if not dry:
                conn.execute(
                    f"UPDATE {t} SET question_id=? WHERE question_id=?",
                    (keeper, loser))
        # delete the loser question
        if not dry:
            conn.execute("DELETE FROM questions WHERE id=?", (loser,))

    # ---- integrity checks ----
    if not dry:
        after_ans = conn.execute("SELECT COUNT(*) FROM user_answers").fetchone()[0]
        after_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        after_q = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        assert after_ans == before_ans, f"ANSWER LOSS! {before_ans}->{after_ans}"
        assert after_users == before_users, f"USER LOSS! {before_users}->{after_users}"
        assert after_q == before_q - len(losers), f"question count off: {after_q}"
        for k in keeper_before:
            now = conn.execute(
                "SELECT COUNT(*) FROM user_answers WHERE question_id=?", (k,)
            ).fetchone()[0]
            exp = keeper_before[k] + gain.get(k, 0)
            assert now == exp, f"keeper {k} answers {now} != expected {exp}"
        # no leftover child rows pointing at losers
        for t in REPOINT_TABLES + ["question_tags", "question_images",
                                   "user_question_state", "user_notes"]:
            left = conn.execute(
                f"SELECT COUNT(*) FROM {t} WHERE question_id IN "
                f"({','.join('?'*len(losers))})", losers).fetchone()[0]
            assert left == 0, f"{t} still has {left} rows on losers"
        conn.commit()
        log(f"APPLIED. answers {before_ans} (unchanged), "
            f"questions {before_q}->{after_q}, users {after_users} (unchanged)")
    else:
        conn.rollback()
        log(f"\nDRY-RUN summary:")
        log(f"  duplicate groups merged: {len(losers)}")
        log(f"  user_answers re-pointed (NOT deleted): {sum(gain.values())}")
        log(f"  user_answers total stays: {before_ans}")
        log(f"  questions: {before_q} -> {before_q - len(losers)}")
        log(f"  users: {before_users} (unchanged)")
    conn.close()


if __name__ == "__main__":
    main()
