import { TabControl, type Item } from '../shared';
export interface TabsV2Props {
  items: Item[]; activeId: string; onChange: (id: string) => void; variant?: 'line' | 'pill';
}
export function TabsV2(props: TabsV2Props) { return <TabControl major={2} {...props} />; }
