<script setup>
import { RouterView } from 'vue-router'
</script>

<template>
  <RouterView />
</template>

<style>
:root { --safe-area-top: env(safe-area-inset-top); }
html { font-size: 17px; }   /* up from default 14-16; cascades via Vant rem */
html, body, #app {
  margin: 0; padding: 0;
  min-height: 100%;
  background: #f7f8fa;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang TC",
               "Microsoft JhengHei", sans-serif;
  font-size: 17px;
  line-height: 1.6;
}
.van-cell { font-size: 16px !important; }
.van-cell__label { font-size: 14px !important; }
.van-button { font-size: 15px !important; }
.van-nav-bar__title { font-size: 17px !important; }
</style>
