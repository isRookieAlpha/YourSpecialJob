<script setup>
// 布局壳：左侧边栏（品牌→统计页 + 历史会话列表 + 底部设置） + 路由视图（KeepAlive 保留各页状态）
import SidebarSessions from './components/SidebarSessions.vue'
</script>

<template>
  <div class="app">
    <aside class="sidebar">
      <RouterLink to="/summary" class="brand">
        <div class="brand-name">YourSpecialJob</div>
        <div class="brand-sub">你的专属兼职</div>
        <div class="brand-sub brand-cta">—>点击这里<—</div>
        <div class="brand-sub">查看已收藏的心动offer</div>
      </RouterLink>

      <SidebarSessions />

      <div class="sidebar-bottom">
        <RouterLink to="/settings" class="nav-link">设置</RouterLink>
      </div>
    </aside>

    <main class="main">
      <RouterView v-slot="{ Component }">
        <KeepAlive>
          <component :is="Component" />
        </KeepAlive>
      </RouterView>
    </main>
  </div>
</template>

<style>
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  background: #f1f5f9;
  color: #0f172a;
  font-family: 'PingFang SC', 'Microsoft YaHei', system-ui, -apple-system, sans-serif;
}
</style>

<style scoped>
.app {
  height: 100vh;
  display: flex;
  gap: 16px;
  margin: 0;
  /* 左侧留 8px 缝隙让卡片浮现；上下 16px → 8px 收窄 */
  padding: 8px 16px 8px 8px;
}
.sidebar {
  width: 224px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: #fff;
  border-radius: 18px;
  padding: 18px 12px 12px;
  box-shadow: 0 6px 20px rgba(30, 41, 59, 0.09);
}
.brand {
  display: block;
  text-decoration: none;
  /* 艺术简约：衬线字体 + 宽字距 + 墨色 */
  padding: 4px 8px 14px;
  font-family: Georgia, 'Times New Roman', 'Songti SC', 'SimSun', serif;
  text-align: center;
  cursor: pointer;
}
.brand-name {
  /* Google Font Nabla（彩色 3D 字体）：字体只提供 400，勿设粗体避免合成模糊 */
  font-family: 'Nabla', system-ui;
  font-optical-sizing: auto;
  font-weight: 400;
  font-style: normal;
  font-variation-settings: 'EDPT' 100, 'EHLT' 12;
  font-size: 22px;
  letter-spacing: 0.04em;
  color: #1f2937;
}
.brand-sub {
  margin-top: 3px;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 11px;
  letter-spacing: 0.18em;
  color: #94a3b8;
}
.brand-cta {
  font-family: 'WDXL Lubrifont SC', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 15px;
  letter-spacing: 0.08em;
  color: #475569;
}
.nav-link {
  display: block;
  text-decoration: none;
  font-size: 14px;
  color: #475569;
  border-radius: 10px;
  padding: 10px 14px;
  transition: background 0.15s, color 0.15s;
}
.nav-link:hover {
  background: #f8fafc;
}
.nav-link.router-link-active {
  background: #eff6ff;
  color: #2563eb;
  font-weight: 600;
}
.sidebar-bottom {
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid #f1f5f9;
}
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>