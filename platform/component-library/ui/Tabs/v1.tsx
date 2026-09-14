import { TabControl, type Item } from '../shared';
export interface TabsV1Props { items: Item[]; activeId: string; onChange: (id: string) => void }
export function TabsV1(props: TabsV1Props) { return <TabControl major={1} {...props} />; }
