import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
const Home=()=>import('./views/Home.vue')
const Result=()=>import('./views/Result.vue')
import Workspace from './views/Workspace.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {path:'/workspace',name:'Workspace',component:Workspace},
    {
      path: '/',
      name: 'Home',
      component: Home
    },
    {
      path: '/result',
      name: 'Result',
      component: Result
    }
  ]
})

const app = createApp(App)

app.use(router)
app.use(Antd)

app.mount('#app')

