import { TextControl } from '../shared';
export interface InputV3Props {
  label: string; value: string; onChange: (value: string) => void;
  placeholder?: string; type?: 'text' | 'number' | 'password' | 'tel'; error?: string; prefix?: string;
}
export function InputV3(props: InputV3Props) {
  return <TextControl major={3} {...props} />;
}
