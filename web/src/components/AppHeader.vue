<script setup>
import { logout, showLobby, state } from '../store'
</script>

<template>
  <header class="app-header">
    <button class="brand" @click="showLobby(true)">
      <span class="brand-mark">BL</span>
      <span><b>BLACKLINE</b><small>TACTICAL ARENA / 02</small></span>
    </button>
    <div class="header-center">
      <span class="connection-badge" :class="{ online: state.connected }">
        <i></i>{{ state.connected ? 'SERVER ONLINE' : 'RECONNECTING' }}
      </span>
      <span v-if="state.rtt !== null" class="rtt">{{ state.rtt }} MS</span>
    </div>
    <div class="header-actions">
      <span class="header-user">{{ state.username }}</span>
      <button class="button ghost small" @click="state.leaderboardOpen = true">排行榜</button>
      <button class="button ghost small" @click="logout">退出</button>
    </div>
  </header>
</template>
