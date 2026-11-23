<script setup>
import { nextTick, ref, watch } from 'vue'
import { sendChat, state } from '../store'

const props = defineProps({ compact: Boolean })
const text = ref('')
const log = ref(null)

function submit() {
  if (!text.value.trim()) return
  sendChat(text.value)
  text.value = ''
}

watch(() => state.chats.length, async () => {
  await nextTick()
  if (log.value) log.value.scrollTop = log.value.scrollHeight
})

function formatTime(timestamp) {
  return new Date((timestamp || Date.now() / 1000) * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <section class="chat-panel" :class="{ compact: props.compact }">
    <div class="section-heading"><h3>房间通信</h3><span>LIVE CHAT</span></div>
    <div ref="log" class="chat-log">
      <p v-if="!state.chats.length" class="chat-empty">加入房间后，与玩家实时通信。</p>
      <div v-for="(item, index) in state.chats" :key="index" class="chat-message">
        <span class="chat-time">{{ formatTime(item.timestamp) }}</span>
        <p><b>{{ item.username }}</b>{{ item.text }}</p>
      </div>
    </div>
    <form class="chat-form" @submit.prevent="submit">
      <input v-model="text" maxlength="200" placeholder="输入消息，回车发送…" />
      <button type="submit">↑</button>
    </form>
  </section>
</template>
