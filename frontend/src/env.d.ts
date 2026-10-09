interface ImportMetaEnv {
  readonly VITE_AUTH_MODE?: "demo" | "api";
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEV_PORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
