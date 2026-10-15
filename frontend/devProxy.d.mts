export interface ApiProxyOptions {
  target: string;
  changeOrigin: true;
}

export declare function resolveApiProxyTarget(value: string | undefined): string | undefined;
export declare function createApiProxyOptions(value: string | undefined): ApiProxyOptions | undefined;

