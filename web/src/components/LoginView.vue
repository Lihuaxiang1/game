<script setup>
import { ref } from 'vue'
import { authenticate, connect, state } from '../store'

const username = ref('')
const password = ref('')

function submit(action) {
  if (!username.value.trim() || !password.value) {
    state.notice = '请输入用户名和密码'
    return
  }
  authenticate(action, username.value.trim(), password.value)
}
</script>

<template>
  <main class="login-page">
    <section class="login-hero">
      <div class="hero-brand"><span class="brand-mark large">BL</span> NETWORK COMBAT LAB</div>
      <div class="hero-copy">
        <p class="eyebrow">SERVER AUTHORITATIVE / 30 HZ</p>
        <h1>黑白之间，<br><span>火线交锋。</span></h1>
        <p class="hero-description">2～5 人实时火柴人枪战。探索立体战区，切换四种特色武器，与好友争夺排行榜首位。</p>
        <div class="hero-tags"><span>2200 × 1400 战区</span><span>WEBSOCKET 同步</span><span>断线恢复</span></div>
      </div>
      <div class="arena-preview" aria-hidden="true">
        <div class="preview-grid"></div>
        <i class="cover c1"></i><i class="cover c2"></i><i class="cover c3"></i><i class="cover c4"></i>
        <svg viewBox="0 0 100 120" class="preview-stick s1"><circle cx="50" cy="18" r="11"/><path d="M50 30v42M50 44l30-13M50 44l-19 16M50 72L32 106M50 72l20 34M76 31h22"/></svg>
        <svg viewBox="0 0 100 120" class="preview-stick s2"><circle cx="50" cy="18" r="11"/><path d="M50 30v42M50 44l30-13M50 44l-19 16M50 72L32 106M50 72l20 34M76 31h22"/></svg>
        <span class="preview-label">LIVE FIRE ZONE / 07</span>
      </div>
      <footer class="hero-footer"><span>BUILD 2.0.0</span><span>C/S ARCHITECTURE</span><span>SQLITE PERSISTENCE</span></footer>
    </section>

    <section class="login-panel">
      <div class="login-card">
        <p class="eyebrow">PLAYER AUTHENTICATION</p>
        <h2>进入战区</h2>
        <p class="muted">登录已有账号，或创建一个新的战斗档案。</p>
        <form @submit.prevent="submit('login')">
          <label>用户名<small>2～16 位字母、数字或中文</small></label>
          <input v-model="username" class="text-input" autocomplete="username" maxlength="16" placeholder="PLAYER ID" />
          <label>密码<small>4～64 位</small></label>
          <input v-model="password" class="text-input" type="password" autocomplete="current-password" maxlength="64" placeholder="••••••••" />
          <button class="button primary login-submit" type="submit">登录并进入 <span>→</span></button>
          <button class="button secondary login-submit" type="button" @click="submit('register')">注册新账号</button>
        </form>
        <div class="login-connection" :class="{ online: state.connected }">
          <i></i>{{ state.connected ? '游戏服务器已连接' : '正在连接游戏服务器' }}
          <button v-if="!state.connected" @click="connect">重试</button>
        </div>
        <div class="login-help"><b>作战操作</b><span>WASD 移动</span><span>鼠标瞄准</span><span>左键射击</span><span>1～4 切枪</span></div>
      </div>
    </section>
  </main>
</template>
