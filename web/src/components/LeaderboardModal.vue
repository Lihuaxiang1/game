<script setup>
import { refresh, state } from '../store'
</script>

<template>
  <div class="modal-backdrop" @click.self="state.leaderboardOpen = false">
    <section class="leaderboard-modal">
      <div class="modal-header">
        <div><p class="eyebrow">GLOBAL COMBAT RECORD</p><h2>积分排行榜</h2></div>
        <button class="icon-button" @click="state.leaderboardOpen = false">×</button>
      </div>
      <div class="leaderboard-head"><span>积分 = 击杀 × 100 − 阵亡 × 20 + 胜利奖励 300</span><button @click="refresh">刷新</button></div>
      <div class="table-wrap">
        <table class="data-table leaderboard-table">
          <thead><tr><th>#</th><th>玩家</th><th>积分</th><th>胜 / 场</th><th>击杀</th><th>阵亡</th></tr></thead>
          <tbody>
            <tr v-for="(entry, index) in state.leaderboard" :key="entry.username" :class="{ highlight: entry.username === state.username }">
              <td><span class="rank-number">{{ String(index + 1).padStart(2, '0') }}</span></td>
              <td><b>{{ entry.username }}</b><small v-if="entry.username === state.username"> YOU</small></td>
              <td><strong>{{ entry.score }}</strong></td><td>{{ entry.wins }} / {{ entry.games }}</td><td>{{ entry.kills }}</td><td>{{ entry.deaths }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!state.leaderboard.length" class="empty-state">暂无战斗记录</p>
      </div>
    </section>
  </div>
</template>
