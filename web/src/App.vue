<script setup>
import { computed, onMounted } from 'vue'
import { connect, state } from './store'
import AppHeader from './components/AppHeader.vue'
import LoginView from './components/LoginView.vue'
import LobbyView from './components/LobbyView.vue'
import GameView from './components/GameView.vue'
import LeaderboardModal from './components/LeaderboardModal.vue'

onMounted(connect)
const showHeader = computed(() => state.screen !== 'login')
</script>

<template>
  <div class="app-shell">
    <AppHeader v-if="showHeader" />
    <LoginView v-if="state.screen === 'login'" />
    <LobbyView v-else-if="state.screen === 'lobby'" />
    <GameView v-else />
    <LeaderboardModal v-if="state.leaderboardOpen" />
    <Transition name="toast">
      <button v-if="state.notice" class="notice-toast" @click="state.notice = ''">
        <span class="status-dot" :class="{ online: state.connected }"></span>
        {{ state.notice }}
        <span class="toast-close">×</span>
      </button>
    </Transition>
  </div>
</template>
