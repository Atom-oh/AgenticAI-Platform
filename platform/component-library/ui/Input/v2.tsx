import { TextControl } from '../shared';
export interface InputV2Props {
  label: string; value: string; onChange: (value: string) => void;
  placeholder?: string; type?: 'text' | 'number' | 'password';
}
export function InputV2(props: InputV2Props) {
  return <TextControl major={2} {...props} />;
}
