import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({ baseURL: '/api/v1', timeout: 60000 })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('pb_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail
    if (status === 401) {
      localStorage.removeItem('pb_token')
      localStorage.removeItem('pb_user')
      if (!location.pathname.startsWith('/login')) location.href = '/login'
    }
    ElMessage.error(typeof detail === 'string' ? detail : '请求失败')
    return Promise.reject(err)
  },
)

export default api
