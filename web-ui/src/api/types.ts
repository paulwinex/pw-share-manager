export interface UserOut {
  id: string;
  username: string;
  created_at: string;
  is_admin: boolean;
}

export interface GroupOut {
  id: string;
  name: string;
  is_personal: boolean;
}

export type AccessLevel = 'ro' | 'rw';

export interface MemberOut {
  user_id: string;
  username: string;
  access_level: AccessLevel;
  expires_at: string | null;
}

export interface ShareOut {
  id: string;
  name: string;
  path: string;
  comment: string | null;
  wide_links: boolean;
}

export interface ShareInput {
  name: string;
  path: string;
  comment: string;
  wide_links: boolean;
}

export interface SyncReport {
  added: string[];
  removed: string[];
  updated: string[];
  params_set: Record<string, string[]>;
}

export interface ExpiringMember {
  user_id: string;
  username: string;
  group_id: string;
  group_name: string;
  access_level: AccessLevel;
  expires_at: string;
}

export interface StatsResponse {
  users_count: number;
  admins_count: number;
  groups_count: number;
  personal_groups_count: number;
  shares_count: number;
  registry_shares_count: number;
  memberships_count: number;
  expiring_memberships: ExpiringMember[];
}

export type RegistryState = Record<string, Record<string, string>>;

export interface ShareAccess {
  name: string;
  path: string;
  access: string;
}

export interface MountScriptResponse {
  username: string;
  host: string;
  port: number;
  shares: ShareAccess[];
  windows_script: string;
  linux_script: string;
}

export interface ConfigResponse {
  nas_host: string;
  nas_port: number;
}

export interface ShareOutMe {
  name: string;
  host: string;
  port: number;
  access: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}
