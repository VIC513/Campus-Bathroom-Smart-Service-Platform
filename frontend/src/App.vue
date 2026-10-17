<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <div class="app-shell">
    <router-view v-slot="{ Component }">
      <transition name="page" mode="out-in">
        <component :is="Component" :key="route.path" />
      </transition>
    </router-view>

    <!-- 底部导航：浴室 / 排队 / 我的 -->
    <van-tabbar route fixed placeholder>
      <van-tabbar-item replace to="/" icon="wap-home-o">浴室</van-tabbar-item>
      <van-tabbar-item replace to="/queue" icon="clock-o">排队</van-tabbar-item>
      <van-tabbar-item replace to="/profile" icon="user-o">我的</van-tabbar-item>
    </van-tabbar>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100%;
}
.page-enter-active,
.page-leave-active {
  transition: opacity 0.18s ease;
}
.page-enter-from,
.page-leave-to {
  opacity: 0;
}
</style>
