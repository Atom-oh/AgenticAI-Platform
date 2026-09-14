import { DateControl } from '../shared';
export interface DatePickerV1Props { label: string; value: string; onChange: (iso: string) => void }
export function DatePickerV1(props: DatePickerV1Props) { return <DateControl major={1} {...props} />; }
