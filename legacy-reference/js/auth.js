/**
 * Hao AI - 认证管理模块
 * 处理用户登录、注册、Token 管理
 * 必须在 api.js 之前加载
 */

const AuthManager = {
    STORAGE_KEY: 'muse_auth_token',
    _loggingOut: false,

    /**
     * 获取当前 Token
     */
    getToken() {
        return localStorage.getItem(this.STORAGE_KEY);
    },
    
    /**
     * 获取当前用户信息
     */
    getUser() {
        try {
            const raw = localStorage.getItem('muse_auth_user');
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    },
    
    /**
     * 保存登录状态
     */
    _saveAuth(token, user) {
        localStorage.setItem(this.STORAGE_KEY, token);
        localStorage.setItem('muse_auth_user', JSON.stringify(user));
    },
    
    /**
     * 清除登录状态
     */
    logout() {
        if (this._loggingOut) return; // 防止递归导致无限刷新
        this._loggingOut = true;

        localStorage.removeItem(this.STORAGE_KEY);
        localStorage.removeItem('muse_auth_user');
        // 清除本地缓存的数据（避免不同用户数据混淆）
        try {
            const keepKeys = ['muse_auth_token', 'muse_auth_user'];
            Object.keys(localStorage).forEach(key => {
                if (key.startsWith('muse_') && !keepKeys.includes(key)) {
                    localStorage.removeItem(key);
                }
            });
        } catch (e) {
            // ignore
        }
        window.location.href = 'index.html';
    },
    
    /**
     * 检查是否已登录
     */
    isLoggedIn() {
        return !!this.getToken();
    },

    /**
     * 发送邮箱验证码
     */
    async sendEmailCode(email, username) {
        const baseUrl = (typeof API_CONFIG !== 'undefined')
            ? API_CONFIG.BASE_URL
            : API_BASE;
        const body = { email };
        if (username) body.username = username;
        const resp = await fetch(`${baseUrl}/api/auth/send-code`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || '发送失败');
        }
        return resp.json();
    },

    /**
     * 注册
     */
    async register(username, email, password) {
        const baseUrl = (typeof API_CONFIG !== 'undefined') 
            ? API_CONFIG.BASE_URL 
            : API_BASE;
        const emailCode = document.getElementById('register-email-code')?.value || '';
        const resp = await fetch(`${baseUrl}/api/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, email, password, email_code: emailCode })
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || '注册失败');
        }
        return resp.json();
    },
    
    /**
     * 登录
     */
    async login(username, password) {
        const baseUrl = (typeof API_CONFIG !== 'undefined')
            ? API_CONFIG.BASE_URL
            : API_BASE;
        const resp = await fetch(`${baseUrl}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || '登录失败');
        }
        const data = await resp.json();
        this._saveAuth(data.access_token, data.user);
        return data;
    },
    
    /**
     * 获取当前用户信息（从 API）
     */
    async fetchMe() {
        const token = this.getToken();
        if (!token) return null;
        const baseUrl = (typeof API_CONFIG !== 'undefined')
            ? API_CONFIG.BASE_URL
            : API_BASE;
        const resp = await fetch(`${baseUrl}/api/auth/me`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!resp.ok) {
            this.logout();
            return null;
        }
        const user = await resp.json();
        localStorage.setItem('muse_auth_user', JSON.stringify(user));
        // 通知界面（侧边栏头像等）用最新用户信息刷新，避免一直显示上一次的缓存
        try {
            document.dispatchEvent(new CustomEvent('user-updated'));
        } catch (e) {
            /* ignore */
        }
        return user;
    }
};

/**
 * 带认证的 fetch 封装
 * 自动从 AuthManager 获取 token 并添加 Authorization header
 */
async function authFetch(url, options = {}) {
    const headers = options.headers || {};
    const token = AuthManager.getToken();
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    return fetch(url, { ...options, headers });
}
