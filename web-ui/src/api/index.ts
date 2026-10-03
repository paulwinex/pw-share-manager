import client from './client';
import type {
  ConfigResponse,
  GroupOut,
  ShareInput,
  LoginResponse,
  MemberOut,
  MountScriptResponse,
  RegistryState,
  ShareOut,
  ShareOutMe,
  StatsResponse,
  SyncReport,
  UserOut,
} from './types';

export const api = {
  // auth
  login: (username: string, password: string) => {
    const form = new URLSearchParams();
    form.set('username', username);
    form.set('password', password);
    return client.post<LoginResponse>('/api/v1/auth/token', form);
  },
  me: () => client.get<UserOut>('/api/v1/auth/me'),
  stats: () => client.get<StatsResponse>('/api/v1/stats'),
  health: () => client.get<{ status: string }>('/api/v1/health'),

  // users
  listUsers: () => client.get<UserOut[]>('/api/v1/users'),
  createUser: (body: { username: string; password: string; is_admin: boolean }) =>
    client.post<UserOut>('/api/v1/users', body),
  changeUserPassword: (id: string, newPassword: string) =>
    client.post<UserOut>(`/api/v1/users/${id}/password`, { new_password: newPassword }),
  deleteUser: (id: string) => client.delete<void>(`/api/v1/users/${id}`),
  mountScript: (username: string) =>
    client.get<MountScriptResponse>(`/api/v1/users/${username}/mount-script`),

  // groups
  listGroups: () => client.get<GroupOut[]>('/api/v1/groups'),
  createGroup: (name: string) => client.post<GroupOut>('/api/v1/groups', { name }),
  deleteGroup: (id: string) => client.delete<void>(`/api/v1/groups/${id}`),
  listMembers: (groupId: string) => client.get<MemberOut[]>(`/api/v1/groups/${groupId}/members`),
  addMember: (
    groupId: string,
    body: { user_id: string; access_level: string; expires_at: string | null }
  ) => client.post<MemberOut>(`/api/v1/groups/${groupId}/members`, body),
  removeMember: (groupId: string, userId: string) =>
    client.delete<void>(`/api/v1/groups/${groupId}/members/${userId}`),
  updateMember: (
    groupId: string,
    userId: string,
    body: { access_level?: string; expires_at?: string | null }
  ) => client.patch<MemberOut>(`/api/v1/groups/${groupId}/members/${userId}`, body),
  listGroupShares: (groupId: string) => client.get<ShareOut[]>(`/api/v1/groups/${groupId}/shares`),
  linkShare: (groupId: string, shareId: string) =>
    client.post<ShareOut>(`/api/v1/groups/${groupId}/shares`, { share_id: shareId }),
  unlinkShare: (groupId: string, shareId: string) =>
    client.delete<void>(`/api/v1/groups/${groupId}/shares/${shareId}`),

  // shares
  listShares: () => client.get<ShareOut[]>('/api/v1/shares'),
  availableDirs: () => client.get<string[]>('/api/v1/shares/available'),
  createShare: (body: ShareInput) => client.post<ShareOut>('/api/v1/shares', body),
  updateShare: (id: string, body: ShareInput) =>
    client.patch<ShareOut>(`/api/v1/shares/${id}`, body),
  deleteShare: (id: string) => client.delete<void>(`/api/v1/shares/${id}`),

  // system
  config: () => client.get<ConfigResponse>('/api/v1/config'),
  sync: () => client.post<SyncReport>('/api/v1/sync'),
  sweep: () =>
    client.post<{ processed: number; sync: SyncReport | null }>('/api/v1/expirations/sweep'),
  registry: () => client.get<RegistryState>('/api/v1/registry/shares'),

  // self-service
  meShares: () => client.get<ShareOutMe[]>('/api/v1/users/me/shares'),
  meMountScript: () => client.get<MountScriptResponse>('/api/v1/users/me/mount-script'),
  changeMyPassword: (newPassword: string) =>
    client.post<void>('/api/v1/users/me/password', { new_password: newPassword }),
  mountScriptUrl: '/api/v1/users/me/mount-script/download/mount-share.py',
  refresh: (refreshToken: string) =>
    client.post<LoginResponse>('/api/v1/auth/refresh', { refresh_token: refreshToken }),
};
