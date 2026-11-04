// Test-only metadata/IL mutations. Reads originals and writes a new fixture folder.
using System;
using System.IO;
using System.Linq;
using dnlib.DotNet;
using dnlib.DotNet.Emit;
using dnlib.DotNet.Writer;

internal static class ManagedProfileFixtures
{
    public static int Main(string[] args) {
        try {
            if(args.Length!=3) throw new ArgumentException("INPUT OUTPUT metadata|layout|encoding|bad-constant|bad-control");
            if(Directory.Exists(args[1])) throw new IOException("Fixture output must be new");
            string[] files={"VirtualDesktop.Android.dll","VirtualDesktop.Mobile.dll","VirtualDesktop.Net.dll","VirtualDesktop.Interfaces.dll"};
            if(!new [] {"metadata","layout","encoding","bad-constant","bad-control"}.Contains(args[2])) throw new ArgumentException("Unknown fixture mode");
            Directory.CreateDirectory(args[1]);
            foreach(string file in files) using(var m=ModuleDefMD.Load(Path.Combine(args[0],file))) {
                if(args[2]=="metadata") {
                    m.Assembly.Version=new Version(1,34,26,0);
                    m.Mvid=Guid.NewGuid();
                    foreach(var attribute in m.Assembly.CustomAttributes.Where(a=>a.AttributeType.FullName=="System.Reflection.AssemblyFileVersionAttribute")) {
                        attribute.ConstructorArguments[0]=new CAArgument(m.CorLibTypes.String,"1.34.26.0");
                    }
                } else if(args[2]=="layout") {
                    var type=new TypeDefUser("LocalProfileTest","UnrelatedMetadataMarker",m.CorLibTypes.Object.TypeDefOrRef);
                    var method=new MethodDefUser("UnrelatedNoop",MethodSig.CreateStatic(m.CorLibTypes.Void),MethodImplAttributes.IL|MethodImplAttributes.Managed,
                        MethodAttributes.Public|MethodAttributes.Static);
                    method.Body=new CilBody(); method.Body.Instructions.Add(Instruction.Create(OpCodes.Ret));
                    type.Methods.Add(method); m.Types.Insert(1,type);
                } else if(args[2]=="encoding") {
                    foreach(var method in m.GetTypes().SelectMany(t=>t.Methods).Where(x=>x.HasBody)) method.Body.SimplifyMacros(method.Parameters);
                } else if(file=="VirtualDesktop.Mobile.dll") {
                    var check=m.GetTypes().Single(t=>t.FullName=="VirtualDesktop.Mobile.UserSettings").Methods.Single(x=>x.Name.String.StartsWith("<GetHasValidIdentityAsync>"));
                    if(args[2]=="bad-constant") {
                        var constant=check.Body.Instructions.Single(i=>i.IsLdcI4() && i.GetLdcI4Value()==1778352230);
                        constant.OpCode=OpCodes.Ldc_I4; constant.Operand=1778352231;
                    } else {
                        var branch=check.Body.Instructions.First(i=>i.OpCode==OpCodes.Brtrue_S || i.OpCode==OpCodes.Brtrue);
                        branch.OpCode=branch.OpCode==OpCodes.Brtrue_S ? OpCodes.Brfalse_S : OpCodes.Brfalse;
                    }
                }
                var options=new ModuleWriterOptions(m);
                if(args[2]!="layout") options.MetadataOptions.Flags=MetadataFlags.PreserveAll;
                m.Write(Path.Combine(args[1],file),options);
            }
            return 0;
        } catch(Exception ex) {Console.Error.WriteLine(ex.Message);return 1;}
    }
}
