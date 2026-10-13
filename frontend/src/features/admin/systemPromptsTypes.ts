export interface SystemPrompt {
  id: string;
  category: string;
  name: string;
  system_prompt: string | null;
  user_prompt: string | null;
  sort_order: number;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface SystemPromptPage {
  data: SystemPrompt[];
  total: number;
}

export interface SystemPromptQuery {
  skip: number;
  limit: number;
  category?: string;
  is_active?: boolean;
}

export interface SystemPromptInput {
  category: string;
  name: string;
  system_prompt: string;
  user_prompt: string;
  sort_order: number;
  is_active: boolean;
}

export interface SystemPromptsApi {
  list(query: SystemPromptQuery, signal?: AbortSignal): Promise<SystemPromptPage>;
  get(id: string, signal?: AbortSignal): Promise<SystemPrompt>;
  create(input: SystemPromptInput, signal?: AbortSignal): Promise<SystemPrompt>;
  update(id: string, input: SystemPromptInput, signal?: AbortSignal): Promise<SystemPrompt>;
  delete(id: string, signal?: AbortSignal): Promise<{ message: string }>;
}
