import { defineStore } from 'pinia'

// Practice session store.
//
// Model:
//   - `pending`: a fetched question awaiting the user's answer (null when none).
//   - `chain`: list of answered entries `{question, answer}`. Order = answer time.
//   - `feedbackIndex`: which chain entry the Feedback view is currently showing.
//
// 上一題 / 下一題 navigation rules:
//   - From Practice (entering an unanswered question): 上一題 if chain.length > 0
//     -> /feedback (showing the most recent chain entry).
//   - From Feedback: 上一題 -> feedbackIndex--; 下一題 -> feedbackIndex++ if not at
//     end, else go to /practice (resumes pending if any, else fetches new).
export const useSessionStore = defineStore('session', {
  state: () => ({
    sessionId: null,
    sessionParams: null,    // { mode, tag_ids, limit, default_confidence, exclude_mastered, shuffle }
    chain: [],              // [{ question, answer }]   — all answered
    pending: null,          // { question } awaiting answer, or null
    feedbackIndex: 0,
    sessionStartedAt: null,
    // In-session relearn queue: { qid, retryAt, attempts }.
    // Pushed on wrong answer; pickable when chain.length >= retryAt.
    // After 1 retry (attempts >= 1) we don't re-push regardless of outcome,
    // so a wrong card resurfaces at most once in the session.
    relearnQueue: []
  }),
  getters: {
    answeredCount: (s) => s.chain.length,
    correctCount: (s) => s.chain.filter(e => e.answer?.is_correct).length,
    totalTimeMs: (s) => s.chain.reduce((sum, e) => sum + (e.answer?.time_ms || 0), 0),
    sessionLimit: (s) => s.sessionParams?.limit || 0,
    sessionFinished: (s) => {
      const limit = s.sessionParams?.limit || 0
      return limit > 0 && s.chain.length >= limit
    },
    canGoPrev: (s) => s.feedbackIndex > 0,
    isAtChainEnd: (s) => s.feedbackIndex >= s.chain.length - 1,
    currentEntry: (s) => s.chain[s.feedbackIndex] || null,
    // qids the picker should skip:
    //   - all answered (correct OR wrong)
    //   - MINUS relearn entries whose retryAt has elapsed (those become priority)
    excludeQids: (s) => {
      const ready = new Set(
        s.relearnQueue
          .filter(r => s.chain.length >= r.retryAt && r.attempts < 1)
          .map(r => r.qid)
      )
      return s.chain
        .map(e => e.question?.question_id)
        .filter(qid => qid != null && !ready.has(qid))
    },
    priorityQids: (s) => s.relearnQueue
      .filter(r => s.chain.length >= r.retryAt && r.attempts < 1)
      .map(r => r.qid)
  },
  actions: {
    startSession (params) {
      this.sessionId = Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
      this.sessionParams = { ...params }
      this.chain = []
      this.pending = null
      this.feedbackIndex = 0
      this.sessionStartedAt = Date.now()
      this.relearnQueue = []
    },
    setPending (question) {
      this.pending = { question }
    },
    clearPending () {
      this.pending = null
    },
    recordAnswer (answer, timeMs) {
      if (!this.pending) return
      const qid = this.pending.question?.question_id
      this.chain.push({
        question: this.pending.question,
        answer: { ...answer, time_ms: timeMs }
      })
      this.pending = null
      this.feedbackIndex = this.chain.length - 1
      if (qid != null) {
        const existing = this.relearnQueue.find(r => r.qid === qid)
        if (answer?.is_correct) {
          if (existing) existing.attempts = 1  // mark consumed → no re-show
        } else {
          if (existing) {
            existing.attempts = 1  // already retried once, don't push again
          } else {
            // Push: re-eligible after N more questions answered. N grows with
            // pool size to avoid back-to-back when pool is large.
            const RETRY_GAP = 5
            this.relearnQueue.push({
              qid,
              retryAt: this.chain.length + RETRY_GAP,
              attempts: 0
            })
          }
        }
      }
    },
    setFeedbackIndex (i) {
      if (i >= 0 && i < this.chain.length) this.feedbackIndex = i
    },
    goPrevFeedback () { if (this.feedbackIndex > 0) this.feedbackIndex-- },
    goNextFeedback () { if (this.feedbackIndex < this.chain.length - 1) this.feedbackIndex++ },
    showLastFeedback () {
      if (this.chain.length > 0) this.feedbackIndex = this.chain.length - 1
    },
    // Mutate an already-answered chain entry's question (e.g. star toggled on Feedback).
    patchChainQuestion (idx, patch) {
      if (this.chain[idx]) {
        this.chain[idx] = {
          ...this.chain[idx],
          question: { ...this.chain[idx].question, ...patch }
        }
      }
    },
    clearSession () {
      this.sessionId = null
      this.sessionParams = null
      this.chain = []
      this.pending = null
      this.feedbackIndex = 0
      this.sessionStartedAt = null
      this.relearnQueue = []
    }
  },
  // Use localStorage so the session survives mobile OS tab-kill (iOS/Android
  // terminate background tabs after ~5 min, which wipes sessionStorage).
  // TTL-based expiry prevents stale sessions from resurfacing days later.
  // NOTE: pending.answer_token has 30-min server TTL; if user resumes after
  // expiry, /practice/answer returns 400 and Practice.vue catches it.
  persist: {
    storage: localStorage,
    key: 'exam-session',
    afterRestore (ctx) {
      const MAX_AGE_MS = 6 * 60 * 60 * 1000  // 6 hours
      const started = ctx.store.sessionStartedAt
      if (started && Date.now() - started > MAX_AGE_MS) {
        ctx.store.$reset()
      }
    }
  }
})
