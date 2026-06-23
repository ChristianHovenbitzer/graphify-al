namespace Demo.Sample;

codeunit 50101 "Demo Formatter"
{
    procedure Wrap(Value: Text): Text
    begin
        exit('[' + Value + ']');
    end;
}
