import { createContext, useContext } from 'react';
import { workspaceClient, type WorkspaceClient } from './client';
import type { Project, Role } from './types';

export const WorkspaceScope = createContext<{ client: WorkspaceClient; project: Project | null; actorId?: string; role: Role | null }>({
  client: workspaceClient, project: null, role: 'owner',
});
export const useWorkspaceScope = () => useContext(WorkspaceScope);
export const useWorkspaceClient = () => useWorkspaceScope().client;
