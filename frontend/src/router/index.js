import { createRouter, createWebHashHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'

// 三页签路由（hash 模式，静态部署友好）
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/chat', name: 'chat', component: ChatView },
    { path: '/summary', name: 'summary', component: () => import('../views/SummaryView.vue') },
    { path: '/settings', name: 'settings', component: () => import('../views/SettingsView.vue') },
  ],
})

export default router