namespace Demo.Sample;

using System.Utilities;

/// <summary>
/// Synthetic AL sample for testing graphify's AL support. Not derived from any
/// real/proprietary codebase. Demonstrates: cross-object typed calls, an event
/// subscription, and an extension target.
/// </summary>
codeunit 50100 "Demo Greeter"
{
    procedure Greet(Name: Text): Text
    var
        Formatter: Codeunit "Demo Formatter";
    begin
        // cross-object typed call -> resolves to "Demo Formatter".Wrap
        exit(Formatter.Wrap('Hello, ' + Name));
    end;

    [EventSubscriber(ObjectType::Codeunit, Codeunit::"Demo Publisher", 'OnAfterStart', '', false, false)]
    local procedure OnAfterStartSubscriber()
    begin
        Message(Greet('world'));
    end;
}
