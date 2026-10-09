import { useState } from "react";
import { ApiConfigurationError } from "../shared/api/config";
import { AuthProvider } from "../features/auth/AuthProvider";
import { Workspace } from "./Workspace";
import {
  createServicesFromEnvironment,
  type WorkspaceServices,
} from "../shared/api/services";

interface AppProps {
  services?: WorkspaceServices;
}

type ServiceInitialization =
  | { services: WorkspaceServices; error: null }
  | { services: null; error: string };

function initializeServices(): ServiceInitialization {
  try {
    return { services: createServicesFromEnvironment(), error: null };
  } catch (error) {
    const message = error instanceof ApiConfigurationError
      ? error.message
      : "工作台启动配置无效。";
    return { services: null, error: message };
  }
}

export function App({ services: providedServices }: AppProps) {
  const [initialized] = useState(initializeServices);
  const services = providedServices ?? initialized.services;

  if (services === null) {
    return (
      <main className="configuration-screen">
        <section className="configuration-card" role="alert">
          <span className="brand-mark" aria-hidden="true">H</span>
          <p className="eyebrow">工作台未启动</p>
          <h1>本地服务配置需要检查</h1>
          <p>{initialized.error ?? "当前运行模式没有对应的服务配置。"}</p>
          <p className="configuration-help">
            演示模式无需配置 API。API 模式请使用回环地址打开页面，并配置本地 /api 地址。
          </p>
        </section>
      </main>
    );
  }

  return (
    <AuthProvider services={services}>
      <Workspace />
    </AuthProvider>
  );
}
