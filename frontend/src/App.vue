<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
/** 管理端（/admin、/admin/login）不显示 C 端底部导航，保持独立后台形态 */
const isAdminRoute = computed(() => route.path.startsWith('/admin'))
</script>

<template>
  <div class="app-shell">
    <router-view v-slot="{ Component }">
      <transition name="page" mode="out-in">
        <component :is="Component" :key="route.path" />
      </transition>
    </router-view>

    <!-- 底部导航：浴室 / 排队 / 我的（管理端不显示） -->
    <van-tabbar v-if="!isAdminRoute" route fixed placeholder>
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
