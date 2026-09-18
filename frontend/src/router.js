import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', component: () => import('./views/Login.vue') },
  {
    path: '/',
    component: () => import('./views/Layout.vue'),
    children: [
      { path: '', redirect: '/projects' },
      { path: 'projects', component: () => import('./views/Projects.vue') },
      { path: 'projects/:id', component: () => import('./views/ProjectDetail.vue') },
      { path: 'projects/:id/items', component: () => import('./views/RequirementItems.vue') },
      { path: 'projects/:id/review', component: () => import('./views/ReviewWorkbench.vue') },
      { path: 'projects/:id/qa', component: () => import('./views/QaChat.vue') },
      { path: 'projects/:id/compare', component: () => import('./views/Compare.vue') },
      { path: 'projects/:id/clearing', component: () => import('./views/Clearing.vue') },
      { path: 'projects/:id/score', component: () => import('./views/ScoreSheet.vue') },
      {
        path: 'projects/:id/score/summary',
        component: () => import('./views/ScoreSummary.vue'),
        meta: { roles: ['admin', 'leader'] },
      },
      {
        path: 'projects/:id/report',
        component: () => import('./views/Report.vue'),
        meta: { roles: ['admin', 'leader'] },
      },
      { path: 'users', component: () => import('./views/Users.vue'), meta: { roles: ['admin'] } },
      { path: 'audit', component: () => import('./views/AuditLogs.vue'), meta: { roles: ['admin', 'observer'] } },
    ],
  },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach((to) => {
  const user = JSON.parse(localStorage.getItem('pb_user') || 'null')
  if (to.path !== '/login' && !user) return '/login'
  if (to.path === '/login' && user) return '/projects'
  if (to.meta.roles && user && !to.meta.roles.includes(user.role)) return '/projects'
  return true
})

export default router
