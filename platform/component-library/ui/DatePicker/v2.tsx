import { DateControl } from '../shared';
export interface DatePickerV2Props {
  label: string; value: string; onChange: (iso: string) => void; min?: string; max?: string;
}
export function DatePickerV2(props: DatePickerV2Props) { return <DateControl major={2} {...props} />; }
