import { TextControl } from '../shared';
export interface InputV1Props { label: string; value: string; onChange: (value: string) => void }
export function InputV1(props: InputV1Props) {
  return <TextControl major={1} {...props} />;
}
