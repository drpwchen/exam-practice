import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from './stores/auth.js'
import { useHelpMode } from './composables/useHelpMode.js'

const routes = [
  { path: '/login', component: () => import('./views/Login.vue') },
  { path: '/change-password', component: () => import('./views/ChangePassword.vue'), meta: { auth: true } },
  { path: '/', component: () => import('./views/Home.vue'), meta: { auth: true } },
  { path: '/setup', component: () => import('./views/PracticeSetup.vue'), meta: { auth: true } },
  { path: '/practice', component: () => import('./views/Practice.vue'), meta: { auth: true } },
  { path: '/feedback', component: () => import('./views/Feedback.vue'), meta: { auth: true } },
  { path: '/dashboard', component: () => import('./views/Dashboard.vue'), meta: { auth: true } },
  { path: '/wrong-bank', component: () => import('./views/WrongBank.vue'), meta: { auth: true } },
  { path: '/cohort-wrong', component: () => import('./views/CohortWrongBank.vue'), meta: { auth: true } },
  { path: '/history', component: () => import('./views/History.vue'), meta: { auth: true } },
  { path: '/question/:id', component: () => import('./views/QuestionDetail.vue'), meta: { auth: true } },
  { path: '/session-summary', component: () => import('./views/SessionSummary.vue'), meta: { auth: true } },
  { path: '/admin', component: () => import('./views/Admin.vue'), meta: { auth: true, admin: true } },
  { path: '/admin/cohort', component: () => import('./views/AdminCohort.vue'), meta: { auth: true, admin: true } },
  { path: '/admin/paraphrase', component: () => import('./views/AdminParaphrase.vue'), meta: { auth: true, admin: true } },
  { path: '/score-projection', component: () => import('./views/ScoreProjection.vue'), meta: { auth: true } },
  { path: '/settings', component: () => import('./views/Settings.vue'), meta: { auth: true } },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore()
  if (to.meta.auth && !auth.token) {
    return next('/login')
  }
  if (to.meta.auth && auth.user?.must_change_password && to.path !== '/change-password') {
    return next('/change-password')
  }
  if (to.meta.admin) {
    if (!auth.user?.is_admin) return next('/')
  }
  next()
})

// Reset 說明模式 on every navigation so each page starts clean; the title "?"
// (HelpButton) reveals that page's help on demand.
router.afterEach(() => {
  useHelpMode().helpMode.value = false
})

export default router
